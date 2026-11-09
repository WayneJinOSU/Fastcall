from collection_agents.services.call_config import normalize_language_code


def build_standard_voicemail_prompt(locale: str) -> str:
    lang_code = normalize_language_code(locale, default="es")
    if lang_code == "zh":
        return (
            "当前场景：已检测到对方是语音信箱或自动答录机。\n"
            "行动要求：\n"
            "1. 忽略之前所有的对话上下文（包括对方可能发出的'喂'等声音）。\n"
            "2. 立即开始留言。\n"
            "3. 触发流程 : 检测到语音信箱 \n"
            "4. 语言要简练。\n"
            "5. 重要：在留言内容的最后，必须且只能输出这个标签用于挂断电话：再见"
        )
    if lang_code == "en":
        return (
            "Current scenario: Voicemail or automatic answering machine detected.\n"
            "Action requirements:\n"
            "1. Ignore all previous conversation context (including sounds like 'hello' that the other party might have made).\n"
            "2. Start leaving a message immediately.\n"
            "3. Trigger flow : Voicemail detected \n"
            "4. Keep the language concise.\n"
            "5. Important: At the end of the message content, you must and can only output this tag to hang up the call: Goodbye"
        )
    if lang_code == "tl":
        return (
            "Kasalukuyang senaryo: Na-detect na ang kabilang linya ay voicemail o automatic answering machine.\n"
            "Mga kinakailangang aksyon:\n"
            "1. Balewalain ang lahat ng naunang konteksto ng usapan (kasama ang mga tunog tulad ng 'hello' na maaaring ginawa ng kabilang linya).\n"
            "2. Agad na mag-iwan ng mensahe.\n"
            "3. I-trigger ang proseso : Na-detect ang voicemail \n"
            "4. Panatilihing maikli at malinaw ang pananalita.\n"
            "5. Mahalaga: Sa dulo ng nilalaman ng mensahe, dapat at tanging ang tag na ito lamang ang ilalabas upang ibaba ang tawag: Paalam"
        )
    return (
        "Escenario actual: Se ha detectado que el interlocutor es un buzón de voz o un contestador automático.\n"
        "Acción requerida:\n"
        "1. Ignora cualquier input anterior del usuario.\n"
        "2. Deja un mensaje de voz inmediatamente.\n"
        "3. Activar el proceso: Buzón de voz detectado\n"
        "4. Sé breve.\n"
        "5. IMPORTANTE: Al finalizar tu mensaje, DEBES escribir exactamente esta etiqueta para colgar: chao"
    )
