from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np
from hmmlearn.hmm import GaussianHMM
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from .db import connect


MODEL_VERSION = "usage-time-v3-gap-regimes"
PRIMARY_FEATURE_NAMES = ("log1p_gap",)
EXPLORATORY_FEATURE_NAMES = ("log1p_gap", "log1p_previous_output", "log1p_next_input")


def _iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    def convert(item: Any):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, (np.floating, np.integer, np.bool_)):
            return item.item()
        raise TypeError(type(item).__name__)

    return json.dumps(value, ensure_ascii=False, default=convert)


def _scope(provider: str = "", account_id: str = "") -> tuple[str, tuple[str, ...]]:
    clauses = ["c.provider=?"] if provider else ["c.provider IN ('chatgpt','gemini')"]
    params = [provider] if provider else []
    if account_id:
        clauses.append("c.account_id=?")
        params.append(account_id)
    return " AND ".join(clauses), tuple(params)


def _scope_key(provider: str = "", account_id: str = "") -> str:
    return f"{provider}::{account_id}" if account_id else provider


def _events(db_path, provider: str = "", account_id: str = "") -> list[dict[str, Any]]:
    scope_sql, params = _scope(provider, account_id)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT m.id,m.conversation_id,m.role,m.direction,m.created_at,m.visible_tokens,m.sequence_index,
                   c.account_id,c.provider
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.is_active_branch=1 AND m.role IN ('user','assistant') AND {scope_sql}
            ORDER BY m.conversation_id,
              CASE WHEN c.provider='wechat' THEN m.created_at ELSE '' END,
              COALESCE(m.sequence_index,2147483647),m.created_at,m.id
            """,
            params,
        ).fetchall()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row["conversation_id"]].append(row)
    events: list[dict[str, Any]] = []
    for conversation_rows in grouped.values():
        for index, row in enumerate(conversation_rows):
            is_anchor = row["direction"] == "outbound" if row["provider"] == "wechat" else row["role"] == "user"
            if not is_anchor or not row["created_at"]:
                continue
            output_tokens = 0
            for following in conversation_rows[index + 1 :]:
                following_anchor = following["direction"] == "outbound" if row["provider"] == "wechat" else following["role"] == "user"
                if following_anchor:
                    break
                if (row["provider"] == "wechat" and following["direction"] == "inbound") or (
                    row["provider"] != "wechat" and following["role"] == "assistant"
                ):
                    output_tokens += int(following["visible_tokens"] or 0)
            stamp = _iso(row["created_at"])
            events.append({
                "id": row["id"], "conversation_id": row["conversation_id"],
                "account_id": row["account_id"], "provider": row["provider"],
                "timestamp": stamp, "epoch": datetime.fromisoformat(stamp).timestamp(),
                "input_tokens": int(row["visible_tokens"] or 0), "output_tokens": output_tokens,
            })
    return sorted(events, key=lambda item: (item["epoch"], item["id"]))


def _gaps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, (previous, following) in enumerate(zip(events, events[1:])):
        seconds = max(0.0, following["epoch"] - previous["epoch"])
        result.append({
            "gap_index": index, "from_message_id": previous["id"], "to_message_id": following["id"],
            "from_timestamp": previous["timestamp"], "to_timestamp": following["timestamp"],
            "gap_seconds": seconds, "log_gap": math.log1p(seconds),
            "prev_input_tokens": previous["input_tokens"], "prev_output_tokens": previous["output_tokens"],
            "next_input_tokens": following["input_tokens"],
            "same_conversation": previous["conversation_id"] == following["conversation_id"],
            "same_account": previous["account_id"] == following["account_id"],
            "same_platform": previous["provider"] == following["provider"],
        })
    return result


def _exploratory_matrix(gaps: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([[row["log_gap"], math.log1p(row["prev_output_tokens"]), math.log1p(row["next_input_tokens"])] for row in gaps], dtype=float)


def semantic_state_order(characteristic_gaps: Sequence[float]) -> list[int]:
    """Return raw component/state IDs ordered from shortest to longest gap."""
    return [int(index) for index in np.argsort(np.asarray(characteristic_gaps, dtype=float), kind="stable")]


def boundary_decisions(probabilities: Sequence[float], threshold: float) -> np.ndarray:
    """Keep posterior/state inference separate from configurable boundary classification."""
    return np.asarray(probabilities, dtype=float) >= float(threshold)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    return None if not values else float(np.percentile(np.asarray(values, dtype=float), percentile))


def _distribution(gaps: list[dict[str, Any]]) -> dict[str, Any]:
    if not gaps:
        return {"histogram": [], "ticks_seconds": [10, 30, 60, 180, 600, 1800, 3600, 10800, 43200, 86400]}
    values = np.asarray([row["log_gap"] for row in gaps], dtype=float)
    bins = min(36, max(12, int(math.sqrt(len(values)))))
    counts, edges = np.histogram(values, bins=bins)
    return {
        "histogram": [{"log_start": float(edges[i]), "log_end": float(edges[i + 1]), "seconds_start": float(np.expm1(edges[i])), "seconds_end": float(np.expm1(edges[i + 1])), "count": int(count)} for i, count in enumerate(counts)],
        "ticks_seconds": [10, 30, 60, 180, 600, 1800, 3600, 10800, 43200, 86400, 259200],
    }


def _associations(gaps: list[dict[str, Any]], raw: np.ndarray) -> dict[str, Any]:
    if not gaps:
        return {"rho_previous_output": None, "rho_next_input": None, "regression": None, "scatter": []}
    gap_values = np.asarray([row["gap_seconds"] for row in gaps], dtype=float)
    previous = np.asarray([row["prev_output_tokens"] for row in gaps], dtype=float)
    next_input = np.asarray([row["next_input_tokens"] for row in gaps], dtype=float)
    rho_prev = spearmanr(gap_values, previous).statistic if len(gaps) > 1 else np.nan
    rho_next = spearmanr(gap_values, next_input).statistic if len(gaps) > 1 else np.nan
    regression = LinearRegression().fit(raw[:, 1:], raw[:, 0]) if len(gaps) > 2 else None
    indexes = np.linspace(0, len(gaps) - 1, min(400, len(gaps)), dtype=int)
    return {
        "rho_previous_output": None if np.isnan(rho_prev) else float(rho_prev),
        "rho_next_input": None if np.isnan(rho_next) else float(rho_next),
        "regression": None if regression is None else {"intercept": float(regression.intercept_), "coefficients": {"log1p_previous_output": float(regression.coef_[0]), "log1p_next_input": float(regression.coef_[1])}, "r_squared": float(regression.score(raw[:, 1:], raw[:, 0]))},
        "scatter": [{"gap_seconds": float(gap_values[i]), "previous_output_tokens": int(previous[i]), "next_input_tokens": int(next_input[i])} for i in indexes],
    }


def _fit_gmm_candidates(values: np.ndarray, random_state: int, maximum: int = 6) -> list[tuple[GaussianMixture, dict[str, Any]]]:
    result = []
    for components in range(1, min(maximum, len(values)) + 1):
        model = GaussianMixture(n_components=components, covariance_type="diag", n_init=3, max_iter=300, reg_covar=1e-5, random_state=random_state).fit(values)
        result.append((model, {"component_or_state_count": components, "log_likelihood": float(model.score(values) * len(values)), "aic": float(model.aic(values)), "bic": float(model.bic(values))}))
    return result


def _regime_label(rank: int, count: int) -> str:
    if rank == 0:
        return "Short-gap"
    if rank == count - 1:
        return "Long-gap"
    return f"Intermediate-gap {rank}"


def _empirical_regimes(gaps: list[dict[str, Any]], raw_assignments: np.ndarray, raw_order: Sequence[int], weights: Sequence[float], emission_centers: Sequence[float]) -> list[dict[str, Any]]:
    details = []
    raw_values = np.asarray([row["gap_seconds"] for row in gaps], dtype=float)
    for rank, raw_id in enumerate(raw_order):
        values = raw_values[raw_assignments == raw_id]
        details.append({
            "semantic_index": rank, "raw_state_id": int(raw_id), "label": _regime_label(rank, len(raw_order)),
            "median_gap_seconds": float(np.median(values)) if len(values) else None,
            "mean_gap_seconds": float(np.mean(values)) if len(values) else None,
            "p25_gap_seconds": float(np.percentile(values, 25)) if len(values) else None,
            "p75_gap_seconds": float(np.percentile(values, 75)) if len(values) else None,
            "empirical_weight": float(len(values) / max(len(gaps), 1)), "model_weight": float(weights[raw_id]),
            "emission_center_gap_seconds": float(emission_centers[raw_id]),
        })
    return details


def build_sessions(events: list[dict[str, Any]], boundaries: Sequence[bool], tail_seconds: int) -> list[dict[str, Any]]:
    """Construct sessions directly from one explicit decision per adjacent event gap."""
    if not events:
        return []
    if len(boundaries) != len(events) - 1:
        raise ValueError("boundaries must contain exactly one decision per adjacent event gap")
    split_points = [0, *(index + 1 for index, is_boundary in enumerate(boundaries) if is_boundary), len(events)]
    result = []
    for session_index, (start, end) in enumerate(zip(split_points, split_points[1:]), 1):
        session_events = events[start:end]
        if not session_events:
            continue
        first, last = session_events[0], session_events[-1]
        internal_gaps = [max(0.0, session_events[i + 1]["epoch"] - session_events[i]["epoch"]) for i in range(len(session_events) - 1)]
        span = max(0.0, last["epoch"] - first["epoch"])
        result.append({
            "session_index": session_index, "start_at": first["timestamp"], "end_at": last["timestamp"],
            "event_count": len(session_events), "session_span_seconds": span, "tail_allowance_seconds": tail_seconds,
            "estimated_usage_seconds": span + tail_seconds, "max_internal_gap_seconds": max(internal_gaps, default=0.0),
            "median_internal_gap_seconds": float(np.median(internal_gaps)) if internal_gaps else 0.0,
            "total_input_tokens": sum(int(event.get("input_tokens") or 0) for event in session_events),
            "total_output_tokens": sum(int(event.get("output_tokens") or 0) for event in session_events),
        })
    return result


def session_duration_diagnostics(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["estimated_usage_seconds"]) for row in sessions]
    return {
        "duration_basis": "estimated_usage_seconds_including_tail_allowance", "total_sessions": len(values),
        "median_seconds": _percentile(values, 50), "mean_seconds": None if not values else float(np.mean(values)),
        "p75_seconds": _percentile(values, 75), "p90_seconds": _percentile(values, 90),
        "p95_seconds": _percentile(values, 95), "p99_seconds": _percentile(values, 99),
        "longest_seconds": None if not values else float(max(values)),
        "over_2h": sum(value > 2 * 3600 for value in values), "over_4h": sum(value > 4 * 3600 for value in values),
        "over_6h": sum(value > 6 * 3600 for value in values), "over_12h": sum(value > 12 * 3600 for value in values),
        "over_24h": sum(value > 24 * 3600 for value in values),
    }


def boundary_diagnostics(gaps: list[dict[str, Any]], boundaries: Sequence[bool]) -> dict[str, Any]:
    decisions = np.asarray(boundaries, dtype=bool)
    if len(decisions) != len(gaps):
        raise ValueError("boundaries must contain exactly one decision per gap")
    values = np.asarray([row["gap_seconds"] for row in gaps], dtype=float)
    within = values[~decisions].tolist()
    breaks = values[decisions].tolist()
    return {
        "total_gaps": len(gaps), "boundaries": int(np.sum(decisions)),
        "boundary_rate": float(np.mean(decisions)) if len(decisions) else 0.0,
        "sessions": int(np.sum(decisions)) + (1 if gaps else 0),
        "median_within_gap_seconds": _percentile(within, 50), "p90_within_gap_seconds": _percentile(within, 90),
        "max_within_gap_seconds": None if not within else float(max(within)),
        "median_break_gap_seconds": _percentile(breaks, 50), "minimum_break_gap_seconds": None if not breaks else float(min(breaks)),
    }


def _signature(db_path, config: dict[str, Any], provider: str = "", account_id: str = "") -> str:
    scope_sql, params = _scope(provider, account_id)
    with connect(db_path) as conn:
        row = conn.execute(
            f"""SELECT COUNT(*) n,MAX(m.created_at) latest,COALESCE(SUM(m.visible_tokens),0) tokens
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.is_active_branch=1 AND m.role='user' AND m.created_at IS NOT NULL AND {scope_sql}""",
            params,
        ).fetchone()
    payload = {"version": MODEL_VERSION, "provider": provider or "ai", "account_id": account_id, "n": row["n"], "latest": row["latest"], "tokens": row["tokens"], "config": config}
    return hashlib.sha256(_json(payload).encode()).hexdigest()


def refresh_usage_time(db_path, config: dict[str, Any], provider: str = "", account_id: str = "") -> dict[str, Any]:
    trained_at = datetime.now(timezone.utc).isoformat()
    tail_seconds = max(0, int(float(config.get("tail_allowance_minutes", 5)) * 60))
    minimum = max(2, int(config.get("min_model_samples", 50)))
    random_state = int(config.get("random_state", 42))
    boundary_threshold = min(1.0, max(0.0, float(config.get("boundary_threshold", 0.5))))
    events, gaps = _events(db_path, provider, account_id), []
    gaps = _gaps(events)
    exploratory = _exploratory_matrix(gaps) if gaps else np.empty((0, len(EXPLORATORY_FEATURE_NAMES)))
    platforms = sorted({event["provider"] for event in events})
    accounts = sorted({event["account_id"] for event in events})
    base_summary: dict[str, Any] = {
        "model_version": MODEL_VERSION, "trained_at": trained_at, "scope_provider": provider, "scope_account_id": account_id,
        "status": "insufficient" if len(gaps) < minimum else "complete", "user_events": len(events),
        "sample_count": len(gaps), "minimum_model_samples": minimum,
        "first_event": events[0]["timestamp"] if events else None, "last_event": events[-1]["timestamp"] if events else None,
        "platforms": platforms, "accounts": accounts, "tail_allowance_seconds": tail_seconds,
        "boundary_threshold": boundary_threshold, "primary_features": list(PRIMARY_FEATURE_NAMES),
        "distribution": _distribution(gaps), "associations": _associations(gaps, exploratory),
        "gmm": None, "hmm": None, "agreement_rate": None, "disagreement_count": 0,
        "mean_probability_difference": None, "confusion_matrix": None, "feature_model_diagnostics": [],
    }
    candidate_rows: list[dict[str, Any]] = []
    session_rows: dict[str, list[dict[str, Any]]] = {"gmm": [], "hmm": []}
    count = len(gaps)
    gmm_probabilities, hmm_probabilities = np.full(count, np.nan), np.full(count, np.nan)
    gmm_components, hmm_states = np.full(count, -1, dtype=int), np.full(count, -1, dtype=int)
    gmm_probability_rows: list[list[float] | None] = [None] * count
    hmm_short_probabilities = np.full(count, np.nan)
    gmm_decisions, hmm_decisions = np.zeros(count, dtype=bool), np.zeros(count, dtype=bool)

    if count >= minimum:
        gap_raw = exploratory[:, [0]]
        gap_scaler = StandardScaler().fit(gap_raw)
        gap_values = gap_scaler.transform(gap_raw)
        candidates = _fit_gmm_candidates(gap_values, random_state, maximum=6)
        aic_index = min(range(len(candidates)), key=lambda i: candidates[i][1]["aic"])
        bic_index = min(range(len(candidates)), key=lambda i: candidates[i][1]["bic"])
        for index, (model, metrics) in enumerate(candidates):
            candidate_rows.append({**metrics, "model_family": "gmm", "feature_set": "gap only", "selected_by_aic": index == aic_index, "selected_by_bic": index == bic_index, "parameters": {"weights": model.weights_, "means": model.means_, "covariances": model.covariances_}})
        selected_gmm = candidates[bic_index][0]
        emission_logs = selected_gmm.means_[:, 0] * gap_scaler.scale_[0] + gap_scaler.mean_[0]
        emission_gaps = np.maximum(0, np.expm1(emission_logs))
        gmm_order = semantic_state_order(emission_gaps)
        raw_gmm_posteriors = selected_gmm.predict_proba(gap_values)
        ordered_gmm_posteriors = raw_gmm_posteriors[:, gmm_order]
        raw_gmm_assignments = selected_gmm.predict(gap_values)
        semantic_by_raw_gmm = {raw_id: rank for rank, raw_id in enumerate(gmm_order)}
        gmm_components = np.asarray([semantic_by_raw_gmm[int(item)] for item in raw_gmm_assignments], dtype=int)
        gmm_probability_rows = ordered_gmm_posteriors.tolist()
        gmm_probabilities = ordered_gmm_posteriors[:, -1]
        gmm_decisions = boundary_decisions(gmm_probabilities, boundary_threshold)
        gmm_sessions = build_sessions(events, gmm_decisions, tail_seconds)
        session_rows["gmm"] = gmm_sessions
        gmm_regimes = _empirical_regimes(gaps, raw_gmm_assignments, gmm_order, selected_gmm.weights_, emission_gaps)
        base_summary["gmm"] = {
            "model_role": "local gap-regime model", "selected_k_aic": candidates[aic_index][0].n_components,
            "selected_k_bic": selected_gmm.n_components, "criteria_disagree": aic_index != bic_index,
            "regimes": gmm_regimes, "boundary_regime": gmm_regimes[-1]["label"], "boundary_threshold": boundary_threshold,
            "sessions": len(gmm_sessions), "session_span_seconds": sum(row["session_span_seconds"] for row in gmm_sessions),
            "estimated_usage_seconds": sum(row["estimated_usage_seconds"] for row in gmm_sessions),
            "boundary_diagnostics": boundary_diagnostics(gaps, gmm_decisions),
            "session_duration_diagnostics": session_duration_diagnostics(gmm_sessions),
        }

        feature_sets = [("gap only", [0]), ("gap + previous output", [0, 1]), ("gap + next input", [0, 2]), ("gap + both token features", [0, 1, 2])]
        for feature_label, columns in feature_sets:
            fitted = candidates if feature_label == "gap only" else _fit_gmm_candidates(StandardScaler().fit_transform(exploratory[:, columns]), random_state, maximum=6)
            if feature_label != "gap only":
                diag_aic = min(range(len(fitted)), key=lambda i: fitted[i][1]["aic"])
                diag_bic = min(range(len(fitted)), key=lambda i: fitted[i][1]["bic"])
                for index, (model, metrics) in enumerate(fitted):
                    candidate_rows.append({**metrics, "model_family": "gmm_diagnostic", "feature_set": feature_label, "selected_by_aic": index == diag_aic, "selected_by_bic": index == diag_bic, "parameters": {"weights": model.weights_, "means": model.means_, "covariances": model.covariances_}})
            best_aic, best_bic = min(fitted, key=lambda item: item[1]["aic"]), min(fitted, key=lambda item: item[1]["bic"])
            base_summary["feature_model_diagnostics"].append({"features": feature_label, "observed_dimensions": len(columns), "selected_k_aic": best_aic[0].n_components, "selected_k_bic": best_bic[0].n_components, "best_aic": best_aic[1]["aic"], "best_bic": best_bic[1]["bic"], "cross_feature_scores_comparable": False})

        hmm = GaussianHMM(n_components=2, covariance_type="diag", n_iter=300, tol=1e-4, random_state=random_state, implementation="scaling").fit(gap_values)
        raw_hmm_posterior = hmm.predict_proba(gap_values)
        hmm_emission_logs = hmm.means_[:, 0] * gap_scaler.scale_[0] + gap_scaler.mean_[0]
        hmm_emission_gaps = np.maximum(0, np.expm1(hmm_emission_logs))
        hmm_order = semantic_state_order(hmm_emission_gaps)
        ordered_hmm_posterior = raw_hmm_posterior[:, hmm_order]
        raw_hmm_assignments = hmm.predict(gap_values)
        semantic_by_raw_hmm = {raw_id: rank for rank, raw_id in enumerate(hmm_order)}
        hmm_states = np.asarray([semantic_by_raw_hmm[int(item)] for item in raw_hmm_assignments], dtype=int)
        hmm_short_probabilities, hmm_probabilities = ordered_hmm_posterior[:, 0], ordered_hmm_posterior[:, -1]
        hmm_decisions = boundary_decisions(hmm_probabilities, boundary_threshold)
        hmm_sessions = build_sessions(events, hmm_decisions, tail_seconds)
        session_rows["hmm"] = hmm_sessions
        ordered_transition = hmm.transmat_[np.ix_(hmm_order, hmm_order)]
        hmm_regimes = _empirical_regimes(
            gaps, raw_hmm_assignments, hmm_order, raw_hmm_posterior.mean(axis=0), hmm_emission_gaps
        )
        hmm_log_likelihood = float(hmm.score(gap_values))
        candidate_rows.append({"model_family": "hmm", "component_or_state_count": 2, "feature_set": "gap only", "log_likelihood": hmm_log_likelihood, "aic": float(14 - 2 * hmm_log_likelihood), "bic": float(7 * math.log(len(gap_values)) - 2 * hmm_log_likelihood), "selected_by_aic": True, "selected_by_bic": True, "parameters": {"start_probability": hmm.startprob_, "transition_matrix": hmm.transmat_, "means": hmm.means_, "covariances": hmm.covars_, "semantic_order": hmm_order}})
        base_summary["hmm"] = {
            "model_role": "sequential gap-regime model", "states": 2, "semantic_state_order": hmm_order,
            "regimes": hmm_regimes, "transition_matrix": ordered_transition,
            "state_persistence": [float(ordered_transition[0, 0]), float(ordered_transition[-1, -1])],
            "boundary_probability_source": "posterior probability of Long-gap regime", "boundary_threshold": boundary_threshold,
            "sessions": len(hmm_sessions), "session_span_seconds": sum(row["session_span_seconds"] for row in hmm_sessions),
            "estimated_usage_seconds": sum(row["estimated_usage_seconds"] for row in hmm_sessions),
            "boundary_diagnostics": boundary_diagnostics(gaps, hmm_decisions),
            "session_duration_diagnostics": session_duration_diagnostics(hmm_sessions),
        }
        differences = np.abs(gmm_probabilities - hmm_probabilities)
        base_summary["agreement_rate"] = float(np.mean(gmm_decisions == hmm_decisions))
        base_summary["disagreement_count"] = int(np.sum(gmm_decisions != hmm_decisions))
        base_summary["mean_probability_difference"] = float(np.mean(differences))
        base_summary["confusion_matrix"] = {
            "gmm_no_hmm_no": int(np.sum(~gmm_decisions & ~hmm_decisions)), "gmm_no_hmm_yes": int(np.sum(~gmm_decisions & hmm_decisions)),
            "gmm_yes_hmm_no": int(np.sum(gmm_decisions & ~hmm_decisions)), "gmm_yes_hmm_yes": int(np.sum(gmm_decisions & hmm_decisions)),
        }

    scope_key = _scope_key(provider, account_id)
    with connect(db_path) as conn:
        run_id = conn.execute("""INSERT INTO usage_time_model_runs(scope_provider,trained_at,status,model_version,sample_count,user_events,first_event,last_event,platforms_json,accounts_json,feature_names_json,feature_transform_json,random_state,tail_allowance_seconds,summary_json,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""", (scope_key, trained_at, base_summary["status"], MODEL_VERSION, len(gaps), len(events), base_summary["first_event"], base_summary["last_event"], _json(platforms), _json(accounts), _json(PRIMARY_FEATURE_NAMES), _json({"transform": "log1p gap then z-score", "scope": account_id or provider or "all AI providers"}), random_state, tail_seconds, _json(base_summary))).lastrowid
        for index, row in enumerate(gaps):
            conn.execute("""INSERT INTO interaction_gaps(run_id,gap_index,from_message_id,to_message_id,from_timestamp,to_timestamp,gap_seconds,log_gap,prev_input_tokens,prev_output_tokens,next_input_tokens,same_conversation,same_account,same_platform,gmm_component,gmm_probabilities_json,gmm_break_probability,gmm_boundary,hmm_state,hmm_short_gap_probability,hmm_long_gap_probability,hmm_break_probability,hmm_boundary) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, index, row["from_message_id"], row["to_message_id"], row["from_timestamp"], row["to_timestamp"], row["gap_seconds"], row["log_gap"], row["prev_input_tokens"], row["prev_output_tokens"], row["next_input_tokens"], int(row["same_conversation"]), int(row["same_account"]), int(row["same_platform"]),
                None if gmm_components[index] < 0 else int(gmm_components[index]), None if gmm_probability_rows[index] is None else _json(gmm_probability_rows[index]), None if np.isnan(gmm_probabilities[index]) else float(gmm_probabilities[index]), None if np.isnan(gmm_probabilities[index]) else int(gmm_decisions[index]),
                None if hmm_states[index] < 0 else int(hmm_states[index]), None if np.isnan(hmm_short_probabilities[index]) else float(hmm_short_probabilities[index]), None if np.isnan(hmm_probabilities[index]) else float(hmm_probabilities[index]), None if np.isnan(hmm_probabilities[index]) else float(hmm_probabilities[index]), None if np.isnan(hmm_probabilities[index]) else int(hmm_decisions[index]),
            ))
        for row in candidate_rows:
            conn.execute("""INSERT INTO usage_time_model_candidates(run_id,model_family,component_or_state_count,feature_set,log_likelihood,aic,bic,selected_by_aic,selected_by_bic,parameters_json) VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id, row["model_family"], row["component_or_state_count"], row["feature_set"], row["log_likelihood"], row["aic"], row["bic"], int(row["selected_by_aic"]), int(row["selected_by_bic"]), _json(row["parameters"])))
        for family, sessions in session_rows.items():
            for row in sessions:
                conn.execute("""INSERT INTO usage_sessions(run_id,model_family,session_index,start_at,end_at,event_count,session_span_seconds,tail_allowance_seconds,estimated_usage_seconds,max_internal_gap_seconds,median_internal_gap_seconds,total_input_tokens,total_output_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, family, row["session_index"], row["start_at"], row["end_at"], row["event_count"], row["session_span_seconds"], row["tail_allowance_seconds"], row["estimated_usage_seconds"], row["max_internal_gap_seconds"], row["median_internal_gap_seconds"], row["total_input_tokens"], row["total_output_tokens"]))
        signature_key = f"usage_time_signature:{scope_key or 'ai'}"
        conn.execute("INSERT INTO app_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (signature_key, _signature(db_path, config, provider, account_id)))
        conn.execute("DELETE FROM usage_time_model_runs WHERE scope_provider=? AND id NOT IN (SELECT id FROM usage_time_model_runs WHERE scope_provider=? ORDER BY id DESC LIMIT 10)", (scope_key, scope_key))
    return {"run_id": run_id, **base_summary}


def ensure_usage_time(db_path, config: dict[str, Any], provider: str = "", account_id: str = "") -> None:
    expected = _signature(db_path, config, provider, account_id)
    scope_key = _scope_key(provider, account_id)
    signature_key = f"usage_time_signature:{scope_key or 'ai'}"
    with connect(db_path) as conn:
        saved = conn.execute("SELECT value FROM app_metadata WHERE key=?", (signature_key,)).fetchone()
        run = conn.execute("SELECT id FROM usage_time_model_runs WHERE scope_provider=? ORDER BY id DESC LIMIT 1", (scope_key,)).fetchone()
    if not run or not saved or saved["value"] != expected:
        refresh_usage_time(db_path, config, provider, account_id)


def _latest(db_path, provider: str = "", account_id: str = "") -> tuple[int | None, dict[str, Any]]:
    scope_key = _scope_key(provider, account_id)
    with connect(db_path) as conn:
        row = conn.execute("SELECT id,summary_json FROM usage_time_model_runs WHERE scope_provider=? ORDER BY id DESC LIMIT 1", (scope_key,)).fetchone()
    return (None, {"status": "never"}) if not row else (row["id"], json.loads(row["summary_json"]))


def usage_summary(db_path, provider: str = "", account_id: str = "") -> dict[str, Any]:
    run_id, summary = _latest(db_path, provider, account_id)
    return {"run_id": run_id, **summary}


def usage_distribution(db_path, provider: str = "", account_id: str = "") -> dict[str, Any]:
    run_id, summary = _latest(db_path, provider, account_id)
    return {"run_id": run_id, **summary.get("distribution", {})}


def usage_associations(db_path, provider: str = "", account_id: str = "") -> dict[str, Any]:
    run_id, summary = _latest(db_path, provider, account_id)
    return {"run_id": run_id, **summary.get("associations", {})}


def usage_candidates(db_path, provider: str = "", account_id: str = "") -> list[dict[str, Any]]:
    run_id, _ = _latest(db_path, provider, account_id)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute("SELECT model_family,component_or_state_count,feature_set,log_likelihood,aic,bic,selected_by_aic,selected_by_bic FROM usage_time_model_candidates WHERE run_id=? ORDER BY model_family,feature_set,component_or_state_count", (run_id,)).fetchall()
    return [dict(row) for row in rows]


def usage_model(db_path, family: str, provider: str = "", account_id: str = "") -> dict[str, Any]:
    run_id, summary = _latest(db_path, provider, account_id)
    return {"run_id": run_id, **(summary.get(family) or {})}


def usage_disagreements(db_path, limit: int = 50, provider: str = "", account_id: str = "") -> list[dict[str, Any]]:
    run_id, _ = _latest(db_path, provider, account_id)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute("""SELECT to_timestamp timestamp,gap_seconds,prev_output_tokens previous_output_tokens,next_input_tokens,gmm_break_probability gmm_boundary_prob,hmm_long_gap_probability hmm_long_gap_prob,gmm_boundary,hmm_boundary,ABS(gmm_break_probability-hmm_long_gap_probability) probability_difference FROM interaction_gaps WHERE run_id=? AND gmm_break_probability IS NOT NULL ORDER BY probability_difference DESC LIMIT ?""", (run_id, max(1, min(limit, 500)))).fetchall()
    return [dict(row) for row in rows]


def usage_boundaries(db_path, limit: int = 240, provider: str = "", account_id: str = "") -> list[dict[str, Any]]:
    run_id, _ = _latest(db_path, provider, account_id)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute("""SELECT * FROM (SELECT gap_index,from_timestamp,to_timestamp,gap_seconds,gmm_break_probability gmm_boundary_prob,hmm_long_gap_probability hmm_long_gap_prob,gmm_boundary,hmm_boundary,CASE WHEN gmm_boundary != hmm_boundary THEN 1 ELSE 0 END disagreement FROM interaction_gaps WHERE run_id=? AND gmm_break_probability IS NOT NULL ORDER BY gap_index DESC LIMIT ?) ORDER BY gap_index""", (run_id, max(1, min(limit, 1000)))).fetchall()
    return [dict(row) for row in rows]


def usage_sessions(db_path, family: str = "gmm", provider: str = "", account_id: str = "") -> list[dict[str, Any]]:
    if family not in {"gmm", "hmm"}:
        raise ValueError("family must be gmm or hmm")
    run_id, _ = _latest(db_path, provider, account_id)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute("""SELECT model_family,session_index,start_at,end_at,event_count,session_span_seconds,tail_allowance_seconds,estimated_usage_seconds,max_internal_gap_seconds,median_internal_gap_seconds,total_input_tokens,total_output_tokens FROM usage_sessions WHERE run_id=? AND model_family=? ORDER BY session_index""", (run_id, family)).fetchall()
    return [dict(row) for row in rows]
