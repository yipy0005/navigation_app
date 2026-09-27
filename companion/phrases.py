"""Pre-translated phrases. These work offline and are checked Italian, unlike machine output."""

PHRASES = [
    {"id": "minimetro", "en": "Where is the Minimetrò?", "zh": "迷你地铁在哪里？",
     "it": "Dov'è il Minimetrò?"},
    {"id": "station", "en": "I am looking for the train station. Can you show me the way, please?",
     "zh": "我在找火车站。请问您能给我指一下路吗？",
     "it": "Sto cercando la stazione ferroviaria. Può indicarmi la direzione, per favore?"},
    {"id": "hotel", "en": "I am trying to get back to my hotel.", "zh": "我想回我的酒店。",
     "it": "Sto cercando di tornare al mio hotel."},
    {"id": "no_italian", "en": "I am a tourist and I don't speak Italian.", "zh": "我是游客，我不会说意大利语。",
     "it": "Sono un turista e non parlo italiano."},
    {"id": "slowly", "en": "Could you speak slowly, please?", "zh": "请您说慢一点，好吗？",
     "it": "Può parlare lentamente, per favore?"},
    {"id": "write", "en": "Could you write it down, please?", "zh": "请您写下来，好吗？",
     "it": "Può scriverlo, per favore?"},
    {"id": "taxi", "en": "Please call a taxi for me.", "zh": "请帮我叫一辆出租车。",
     "it": "Per favore, mi chiami un taxi."},
    {"id": "toilet", "en": "Where is the toilet?", "zh": "洗手间在哪里？",
     "it": "Dov'è il bagno?"},
    {"id": "lift", "en": "Is there a lift or an escalator?", "zh": "这里有电梯或自动扶梯吗？",
     "it": "C'è un ascensore o una scala mobile?"},
    {"id": "sit", "en": "Is there somewhere I can sit down?", "zh": "有地方可以坐一下吗？",
     "it": "C'è un posto dove posso sedermi?"},
    {"id": "pharmacy", "en": "Where is the nearest pharmacy?", "zh": "最近的药店在哪里？",
     "it": "Dov'è la farmacia più vicina?"},
    {"id": "doctor", "en": "I need a doctor.", "zh": "我需要看医生。",
     "it": "Ho bisogno di un medico."},
    {"id": "how_much", "en": "How much is it?", "zh": "多少钱？",
     "it": "Quanto costa?"},
    {"id": "bill", "en": "The bill, please.", "zh": "请结账。",
     "it": "Il conto, per favore."},
    {"id": "water", "en": "A glass of water, please.", "zh": "请给我一杯水。",
     "it": "Un bicchiere d'acqua, per favore."},
    {"id": "thanks", "en": "Thank you very much.", "zh": "非常感谢。",
     "it": "Grazie mille."},
]


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


_INDEX = {_norm(p[lang]): p["it"] for p in PHRASES for lang in ("en", "zh")}


def lookup(text: str) -> str | None:
    return _INDEX.get(_norm(text))
