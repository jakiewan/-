def format_exchange(user: str, assistant: str) -> str:
    return f"用户：{user}\n助手：{assistant}"


def previous_exchange(messages: list) -> str:
    previous_user = next((i for i in range(len(messages) - 2, -1, -1) if messages[i].type == "human"), None)
    if previous_user is None:
        return ""
    answers = [message for message in messages[previous_user + 1:-1] if message.type == "ai" and message.text and not message.tool_calls]
    if not answers:
        return ""
    return format_exchange(messages[previous_user].text, answers[-1].text)


def recent_exchanges(messages: list, max_turns: int = 3) -> str:
    """保留最近完整问答，供连续追问沿用型号和业务背景。"""
    starts = [i for i, message in enumerate(messages[:-1]) if message.type == 'human'][-max_turns:]
    exchanges = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(messages) - 1
        answers = [message.text for message in messages[start + 1:end] if message.type == 'ai' and message.text and not message.tool_calls]
        if answers:
            exchanges.append(format_exchange(messages[start].text, answers[-1]))
    return '\n\n'.join(exchanges)


def encode_intent_inputs(tokenizer, texts: list[str], histories: list[str], context_turns: int, max_length: int):
    questions = tokenizer([f"用户：{text}" if context_turns else text for text in texts], add_special_tokens=False, verbose=False)["input_ids"]
    contexts = tokenizer(histories if context_turns else [""] * len(texts), add_special_tokens=False, verbose=False)["input_ids"]
    encoded = []
    for question, history in zip(questions, contexts):
        # 优先保留本轮问题；历史从上一轮用户消息开始保留，避免长助手回复挤掉用户诉求。
        question = question[:max_length - 3]
        history = history[:max_length - 3 - len(question)]
        encoded.append(tokenizer.prepare_for_model(question, pair_ids=history, truncation=False))
    return tokenizer.pad(encoded, padding=True, return_tensors="pt")
