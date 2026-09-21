export type Summary = {
  conversations: number
  prompts: number
  prompt_visible_tokens: number
  response_visible_tokens: number
  total_visible_tokens: number
  active_days: number
  first_activity: string | null
  latest_activity: string | null
}

export type Day = {
  date: string
  prompts: number
  prompt_visible_tokens: number
  response_visible_tokens: number
  total_visible_tokens: number
  active_conversations: number
  new_conversations: number
}

export type Conversation = {
  id: string
  account_id: string
  account_name: string
  title: string
  created_at: string | null
  updated_at: string | null
  model_hint: string | null
  prompts: number
  turns: number
  prompt_visible_tokens: number
  response_visible_tokens: number
  total_visible_tokens: number
}

export type Account = {
  id: string
  name: string
  browser_profile: string
  enabled: boolean
  conversations?: number
}

export type Message = {
  id: string
  role: string
  created_at: string | null
  model: string | null
  visible_text: string
  visible_tokens: number
  has_attachment: number
}

export type ConversationDetail = Conversation & { messages: Message[]; topics: Topic[] }

export type Topic = {
  id: number
  name: string
  prompt_share: number
  token_share: number
  prompt_percent: number
  token_percent: number
}

export type TopicTimeline = {
  period: string
  topic_id: number
  topic: string
  prompt_share: number
  token_share: number
}
