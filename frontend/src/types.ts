export type Summary = {
  conversations: number
  prompts: number
  outbound_messages: number
  inbound_messages: number
  total_messages: number
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
  turns: number
  outbound_messages: number
  inbound_messages: number
  total_messages: number
  prompt_visible_tokens: number
  response_visible_tokens: number
  total_visible_tokens: number
  active_conversations: number
  new_conversations: number
}

export type Conversation = {
  id: string
  provider: 'chatgpt' | 'gemini' | 'wechat'
  account_id: string
  account_name: string
  account_alias?: string
  account_display_name?: string
  title: string
  created_at: string | null
  updated_at: string | null
  model_hint: string | null
  conversation_type?: 'private' | 'group' | null
  prompts: number
  turns: number
  outbound_messages: number
  inbound_messages: number
  total_messages: number
  prompt_visible_tokens: number
  response_visible_tokens: number
  total_visible_tokens: number
  calendar_span_days?: number
  active_days?: number
  session_count?: number
  estimated_active_seconds?: number
  longest_gap_seconds?: number
  topics?: Pick<Topic, 'id' | 'name' | 'color'>[]
}

export type Account = {
  id: string
  name: string
  browser_profile?: string
  wechat_data_dir?: string
  alias?: string
  external_user_id?: string
  display_name?: string
  provider: 'chatgpt' | 'gemini' | 'wechat'
  enabled: boolean
  conversations?: number
}

export type Message = {
  id: string
  role: string
  direction?: 'outbound' | 'inbound' | 'system' | null
  sender_external_id?: string | null
  sender_display_name?: string | null
  raw_type?: string | null
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
  color: string
}

export type TopicTimeline = {
  period: string
  topic_id: number
  topic: string
  prompt_share: number
  token_share: number
  topic_color: string
}

export type DayConversation = Pick<Conversation, 'id' | 'provider' | 'account_id' | 'account_name' | 'account_display_name' | 'title' | 'conversation_type' | 'prompts' | 'outbound_messages' | 'inbound_messages' | 'total_messages' | 'prompt_visible_tokens' | 'response_visible_tokens' | 'total_visible_tokens'> & {
  first_activity: string
  last_activity: string
  dominant_topic: string | null
  topic_color: string | null
}
