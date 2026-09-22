from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
from hmmlearn.hmm import GaussianHMM
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from .db import connect


MODEL_VERSION = "usage-time-v2"
FEATURE_NAMES = ("log1p_gap", "log1p_previous_output", "log1p_next_input")


def _iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    def convert(item: Any):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, (np.floating, np.integer)):
            return item.item()
        raise TypeError(type(item).__name__)

    return json.dumps(value, ensure_ascii=False, default=convert)


def _events(db_path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT m.id,m.conversation_id,m.role,m.created_at,m.visible_tokens,m.sequence_index,
                   c.account_id,c.provider
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.is_active_branch=1 AND m.role IN ('user','assistant')
            ORDER BY m.conversation_id,COALESCE(m.sequence_index,2147483647),m.created_at,m.id
            """
        ).fetchall()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row["conversation_id"]].append(row)
    events: list[dict[str, Any]] = []
    for conversation_rows in grouped.values():
        for index, row in enumerate(conversation_rows):
            if row["role"] != "user" or not row["created_at"]:
                continue
            output_tokens = 0
            for following in conversation_rows[index + 1:]:
                if following["role"] == "user":
                    break
                if following["role"] == "assistant":
                    output_tokens += int(following["visible_tokens"] or 0)
            stamp = _iso(row["created_at"])
            events.append({
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "account_id": row["account_id"],
                "provider": row["provider"],
                "timestamp": stamp,
                "epoch": datetime.fromisoformat(stamp).timestamp(),
                "input_tokens": int(row["visible_tokens"] or 0),
                "output_tokens": output_tokens,
            })
    return sorted(events, key=lambda item: (item["epoch"], item["id"]))


def _gaps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for previous, following in zip(events, events[1:]):
        result.append({
            "from_message_id": previous["id"],
            "to_message_id": following["id"],
            "from_timestamp": previous["timestamp"],
            "to_timestamp": following["timestamp"],
            "gap_seconds": max(0.0, following["epoch"] - previous["epoch"]),
            "prev_input_tokens": previous["input_tokens"],
            "prev_output_tokens": previous["output_tokens"],
            "next_input_tokens": following["input_tokens"],
            "same_conversation": previous["conversation_id"] == following["conversation_id"],
            "same_account": previous["account_id"] == following["account_id"],
            "same_platform": previous["provider"] == following["provider"],
        })
    return result


def _matrix(gaps: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([
        [
            math.log1p(row["gap_seconds"]),
            math.log1p(row["prev_output_tokens"]),
            math.log1p(row["next_input_tokens"]),
        ]
        for row in gaps
    ], dtype=float)


def _distribution(gaps: list[dict[str, Any]]) -> dict[str, Any]:
    if not gaps:
        return {"histogram": [], "ticks_seconds": [10, 30, 60, 180, 600, 1800, 3600, 10800, 43200, 86400]}
    values = np.log1p([row["gap_seconds"] for row in gaps])
    bins = min(36, max(12, int(math.sqrt(len(values)))))
    counts, edges = np.histogram(values, bins=bins)
    histogram = [
        {
            "log_start": float(edges[index]),
            "log_end": float(edges[index + 1]),
            "seconds_start": float(np.expm1(edges[index])),
            "seconds_end": float(np.expm1(edges[index + 1])),
            "count": int(count),
        }
        for index, count in enumerate(counts)
    ]
    return {"histogram": histogram, "ticks_seconds": [10, 30, 60, 180, 600, 1800, 3600, 10800, 43200, 86400, 259200]}


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
    scatter = [{
        "gap_seconds": float(gap_values[index]),
        "previous_output_tokens": int(previous[index]),
        "next_input_tokens": int(next_input[index]),
    } for index in indexes]
    return {
        "rho_previous_output": None if np.isnan(rho_prev) else float(rho_prev),
        "rho_next_input": None if np.isnan(rho_next) else float(rho_next),
        "regression": None if regression is None else {
            "intercept": float(regression.intercept_),
            "coefficients": {
                "log1p_previous_output": float(regression.coef_[0]),
                "log1p_next_input": float(regression.coef_[1]),
            },
            "r_squared": float(regression.score(raw[:, 1:], raw[:, 0])),
        },
        "scatter": scatter,
    }


def _fit_gmm_candidates(values: np.ndarray, random_state: int) -> list[tuple[GaussianMixture, dict[str, Any]]]:
    maximum = min(4, len(values))
    result = []
    for components in range(1, maximum + 1):
        model = GaussianMixture(
            n_components=components,
            covariance_type="diag",
            n_init=3,
            max_iter=300,
            reg_covar=1e-5,
            random_state=random_state,
        ).fit(values)
        result.append((model, {
            "component_or_state_count": components,
            "log_likelihood": float(model.score(values) * len(values)),
            "aic": float(model.aic(values)),
            "bic": float(model.bic(values)),
        }))
    return result


def _component_details(model: GaussianMixture, scaler: StandardScaler) -> list[dict[str, Any]]:
    order = np.argsort(model.means_[:, 0])
    details = []
    for rank, component in enumerate(order):
        raw_mean = model.means_[component] * scaler.scale_ + scaler.mean_
        details.append({
            "component": int(component),
            "label": "Short-gap" if rank == 0 else "Long-gap" if rank == len(order) - 1 else f"Intermediate {rank}",
            "weight": float(model.weights_[component]),
            "mean_log_gap": float(raw_mean[0]),
            "approximate_gap_seconds": float(max(0, np.expm1(raw_mean[0]))),
            "approximate_previous_output_tokens": float(max(0, np.expm1(raw_mean[1]))),
            "approximate_next_input_tokens": float(max(0, np.expm1(raw_mean[2]))),
        })
    return details


def _sessions(events: list[dict[str, Any]], break_probabilities: np.ndarray, tail_seconds: int) -> list[dict[str, Any]]:
    if not events:
        return []
    boundaries = [0]
    boundaries.extend(index + 1 for index, probability in enumerate(break_probabilities) if probability >= 0.5)
    boundaries.append(len(events))
    result = []
    for session_index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        if start >= end:
            continue
        first, last = events[start], events[end - 1]
        span = max(0.0, last["epoch"] - first["epoch"])
        result.append({
            "session_index": session_index,
            "start_at": first["timestamp"],
            "end_at": last["timestamp"],
            "event_count": end - start,
            "session_span_seconds": span,
            "estimated_usage_seconds": span + tail_seconds,
        })
    return result


def _signature(db_path, config: dict[str, Any]) -> str:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) n,MAX(created_at) latest,COALESCE(SUM(visible_tokens),0) tokens "
            "FROM messages WHERE is_active_branch=1 AND role='user' AND created_at IS NOT NULL"
        ).fetchone()
    payload = {"version": MODEL_VERSION, "n": row["n"], "latest": row["latest"], "tokens": row["tokens"], "config": config}
    return hashlib.sha256(_json(payload).encode()).hexdigest()


def refresh_usage_time(db_path, config: dict[str, Any]) -> dict[str, Any]:
    trained_at = datetime.now(timezone.utc).isoformat()
    tail_seconds = max(0, int(float(config.get("tail_allowance_minutes", 5)) * 60))
    minimum = max(2, int(config.get("min_model_samples", 50)))
    random_state = int(config.get("random_state", 42))
    events = _events(db_path)
    gaps = _gaps(events)
    raw = _matrix(gaps) if gaps else np.empty((0, len(FEATURE_NAMES)))
    platforms = sorted({event["provider"] for event in events})
    accounts = sorted({event["account_id"] for event in events})
    distribution = _distribution(gaps)
    associations = _associations(gaps, raw)
    base_summary: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "trained_at": trained_at,
        "status": "insufficient" if len(gaps) < minimum else "complete",
        "user_events": len(events),
        "sample_count": len(gaps),
        "minimum_model_samples": minimum,
        "first_event": events[0]["timestamp"] if events else None,
        "last_event": events[-1]["timestamp"] if events else None,
        "platforms": platforms,
        "accounts": accounts,
        "tail_allowance_seconds": tail_seconds,
        "distribution": distribution,
        "associations": associations,
        "gmm": None,
        "hmm": None,
        "agreement_rate": None,
        "disagreement_count": 0,
        "mean_probability_difference": None,
        "ablation": [],
    }
    candidate_rows: list[dict[str, Any]] = []
    session_rows: dict[str, list[dict[str, Any]]] = {"gmm": [], "hmm": []}
    gmm_probabilities = np.full(len(gaps), np.nan)
    hmm_probabilities = np.full(len(gaps), np.nan)

    if len(gaps) >= minimum:
        scaler = StandardScaler().fit(raw)
        values = scaler.transform(raw)
        candidates = _fit_gmm_candidates(values, random_state)
        aic_index = min(range(len(candidates)), key=lambda index: candidates[index][1]["aic"])
        bic_index = min(range(len(candidates)), key=lambda index: candidates[index][1]["bic"])
        for index, (model, metrics) in enumerate(candidates):
            candidate_rows.append({
                **metrics,
                "model_family": "gmm",
                "feature_set": "gap + previous output + next input",
                "selected_by_aic": index == aic_index,
                "selected_by_bic": index == bic_index,
                "parameters": {"weights": model.weights_, "means": model.means_, "covariances": model.covariances_},
            })
        selected_gmm = candidates[bic_index][0]
        longest = int(np.argmax(selected_gmm.means_[:, 0]))
        gmm_probabilities = selected_gmm.predict_proba(values)[:, longest]
        gmm_sessions = _sessions(events, gmm_probabilities, tail_seconds)
        session_rows["gmm"] = gmm_sessions
        base_summary["gmm"] = {
            "selected_k_aic": candidates[aic_index][0].n_components,
            "selected_k_bic": selected_gmm.n_components,
            "criteria_disagree": aic_index != bic_index,
            "components": _component_details(selected_gmm, scaler),
            "break_component": longest,
            "sessions": len(gmm_sessions),
            "session_span_seconds": sum(row["session_span_seconds"] for row in gmm_sessions),
            "estimated_usage_seconds": sum(row["estimated_usage_seconds"] for row in gmm_sessions),
        }

        feature_sets = [
            ("gap only", [0]),
            ("gap + previous output", [0, 1]),
            ("gap + next input", [0, 2]),
            ("gap + both token features", [0, 1, 2]),
        ]
        for feature_label, columns in feature_sets:
            subset = StandardScaler().fit_transform(raw[:, columns])
            fitted = _fit_gmm_candidates(subset, random_state)
            best = min(fitted, key=lambda item: item[1]["bic"])
            base_summary["ablation"].append({"features": feature_label, **best[1]})
            candidate_rows.append({
                **best[1], "model_family": "gmm_ablation", "feature_set": feature_label,
                "selected_by_aic": False, "selected_by_bic": True,
                "parameters": {"weights": best[0].weights_, "means": best[0].means_, "covariances": best[0].covariances_},
            })
        gap_only_bic = next(row["bic"] for row in base_summary["ablation"] if row["features"] == "gap only")
        full_bic = next(row["bic"] for row in base_summary["ablation"] if row["features"] == "gap + both token features")
        base_summary["token_feature_bic_improvement"] = float(gap_only_bic - full_bic)

        hmm = GaussianHMM(
            n_components=2, covariance_type="diag", n_iter=300, tol=1e-4,
            random_state=random_state, implementation="scaling",
        ).fit(values)
        posterior = hmm.predict_proba(values)
        state_order = np.argsort(hmm.means_[:, 0])
        active_state, break_state = int(state_order[0]), int(state_order[-1])
        hmm_probabilities = posterior[:, break_state]
        hmm_sessions = _sessions(events, hmm_probabilities, tail_seconds)
        session_rows["hmm"] = hmm_sessions
        transition = hmm.transmat_[np.ix_(state_order, state_order)]
        hmm_log_likelihood = float(hmm.score(values))
        hmm_parameters = (2 - 1) + 2 * (2 - 1) + 2 * values.shape[1] * 2
        candidate_rows.append({
            "model_family": "hmm", "component_or_state_count": 2,
            "feature_set": "gap + previous output + next input",
            "log_likelihood": hmm_log_likelihood,
            "aic": float(2 * hmm_parameters - 2 * hmm_log_likelihood),
            "bic": float(hmm_parameters * math.log(len(values)) - 2 * hmm_log_likelihood),
            "selected_by_aic": True, "selected_by_bic": True,
            "parameters": {"start_probability": hmm.startprob_, "transition_matrix": hmm.transmat_, "means": hmm.means_, "covariances": hmm.covars_},
        })
        raw_state_means = hmm.means_ * scaler.scale_ + scaler.mean_
        base_summary["hmm"] = {
            "states": 2,
            "active_state": active_state,
            "break_state": break_state,
            "transition_matrix": transition,
            "state_gap_seconds": [float(max(0, np.expm1(raw_state_means[state, 0]))) for state in state_order],
            "sessions": len(hmm_sessions),
            "session_span_seconds": sum(row["session_span_seconds"] for row in hmm_sessions),
            "estimated_usage_seconds": sum(row["estimated_usage_seconds"] for row in hmm_sessions),
        }
        decisions_gmm = gmm_probabilities >= 0.5
        decisions_hmm = hmm_probabilities >= 0.5
        differences = np.abs(gmm_probabilities - hmm_probabilities)
        base_summary["agreement_rate"] = float(np.mean(decisions_gmm == decisions_hmm))
        base_summary["disagreement_count"] = int(np.sum(decisions_gmm != decisions_hmm))
        base_summary["mean_probability_difference"] = float(np.mean(differences))

    with connect(db_path) as conn:
        run_id = conn.execute(
            """INSERT INTO usage_time_model_runs(
            trained_at,status,model_version,sample_count,user_events,first_event,last_event,platforms_json,
            accounts_json,feature_names_json,feature_transform_json,random_state,tail_allowance_seconds,summary_json,error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (
                trained_at, base_summary["status"], MODEL_VERSION, len(gaps), len(events),
                base_summary["first_event"], base_summary["last_event"], _json(platforms), _json(accounts),
                _json(FEATURE_NAMES), _json({"transform": "log1p then z-score", "scope": "global user event timeline"}),
                random_state, tail_seconds, _json(base_summary),
            ),
        ).lastrowid
        for row, gmm_probability, hmm_probability in zip(gaps, gmm_probabilities, hmm_probabilities):
            conn.execute(
                """INSERT INTO interaction_gaps(
                run_id,from_message_id,to_message_id,from_timestamp,to_timestamp,gap_seconds,
                prev_input_tokens,prev_output_tokens,next_input_tokens,same_conversation,same_account,same_platform,
                gmm_break_probability,hmm_break_probability) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, row["from_message_id"], row["to_message_id"], row["from_timestamp"], row["to_timestamp"],
                 row["gap_seconds"], row["prev_input_tokens"], row["prev_output_tokens"], row["next_input_tokens"],
                 int(row["same_conversation"]), int(row["same_account"]), int(row["same_platform"]),
                 None if np.isnan(gmm_probability) else float(gmm_probability),
                 None if np.isnan(hmm_probability) else float(hmm_probability)),
            )
        for row in candidate_rows:
            conn.execute(
                """INSERT INTO usage_time_model_candidates(
                run_id,model_family,component_or_state_count,feature_set,log_likelihood,aic,bic,
                selected_by_aic,selected_by_bic,parameters_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, row["model_family"], row["component_or_state_count"], row["feature_set"],
                 row["log_likelihood"], row["aic"], row["bic"], int(row["selected_by_aic"]),
                 int(row["selected_by_bic"]), _json(row["parameters"])),
            )
        for family, sessions in session_rows.items():
            for row in sessions:
                conn.execute(
                    "INSERT INTO usage_sessions(run_id,model_family,session_index,start_at,end_at,event_count,session_span_seconds,estimated_usage_seconds) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, family, row["session_index"], row["start_at"], row["end_at"], row["event_count"], row["session_span_seconds"], row["estimated_usage_seconds"]),
                )
        conn.execute(
            "INSERT INTO app_metadata(key,value) VALUES('usage_time_signature',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_signature(db_path, config),),
        )
        conn.execute("DELETE FROM usage_time_model_runs WHERE id NOT IN (SELECT id FROM usage_time_model_runs ORDER BY id DESC LIMIT 10)")
    return {"run_id": run_id, **base_summary}


def ensure_usage_time(db_path, config: dict[str, Any]) -> None:
    expected = _signature(db_path, config)
    with connect(db_path) as conn:
        saved = conn.execute("SELECT value FROM app_metadata WHERE key='usage_time_signature'").fetchone()
        run = conn.execute("SELECT id FROM usage_time_model_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not run or not saved or saved["value"] != expected:
        refresh_usage_time(db_path, config)


def _latest(db_path) -> tuple[int | None, dict[str, Any]]:
    with connect(db_path) as conn:
        row = conn.execute("SELECT id,summary_json FROM usage_time_model_runs ORDER BY id DESC LIMIT 1").fetchone()
    return (None, {"status": "never"}) if not row else (row["id"], json.loads(row["summary_json"]))


def usage_summary(db_path) -> dict[str, Any]:
    run_id, summary = _latest(db_path)
    return {"run_id": run_id, **summary}


def usage_distribution(db_path) -> dict[str, Any]:
    run_id, summary = _latest(db_path)
    return {"run_id": run_id, **summary.get("distribution", {})}


def usage_associations(db_path) -> dict[str, Any]:
    run_id, summary = _latest(db_path)
    return {"run_id": run_id, **summary.get("associations", {})}


def usage_candidates(db_path) -> list[dict[str, Any]]:
    run_id, _ = _latest(db_path)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT model_family,component_or_state_count,feature_set,log_likelihood,aic,bic,selected_by_aic,selected_by_bic FROM usage_time_model_candidates WHERE run_id=? ORDER BY model_family,feature_set,component_or_state_count",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def usage_model(db_path, family: str) -> dict[str, Any]:
    run_id, summary = _latest(db_path)
    return {"run_id": run_id, **(summary.get(family) or {})}


def usage_disagreements(db_path, limit: int = 50) -> list[dict[str, Any]]:
    run_id, _ = _latest(db_path)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT to_timestamp time,gap_seconds,prev_output_tokens,next_input_tokens,
            gmm_break_probability,hmm_break_probability,
            ABS(gmm_break_probability-hmm_break_probability) difference
            FROM interaction_gaps WHERE run_id=? AND gmm_break_probability IS NOT NULL
            ORDER BY difference DESC LIMIT ?""",
            (run_id, max(1, min(limit, 500))),
        ).fetchall()
    return [dict(row) for row in rows]


def usage_sessions(db_path, family: str = "gmm") -> list[dict[str, Any]]:
    if family not in {"gmm", "hmm"}:
        raise ValueError("family must be gmm or hmm")
    run_id, _ = _latest(db_path)
    if run_id is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT model_family,session_index,start_at,end_at,event_count,session_span_seconds,estimated_usage_seconds FROM usage_sessions WHERE run_id=? AND model_family=? ORDER BY session_index",
            (run_id, family),
        ).fetchall()
    return [dict(row) for row in rows]
