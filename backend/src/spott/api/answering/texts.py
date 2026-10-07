"""What the assistant says by code rather than by the model, in each answer language: fixed answers, the summaries
of the trace's steps, the introductions of contacts."""

import re

NOT_FOUND = {
    "ro": "În documentele publice ale Primăriei disponibile asistentului nu există informații despre această "
          "întrebare. Răspund doar pe baza documentelor, deci nu voi ghici.",
    "ru": "В публичных документах Примэрии, доступных ассистенту, нет информации по этому вопросу. "
          "Я отвечаю только по документам, поэтому не буду угадывать.",
    "en": "The City Hall's public documents available to the assistant have no information on this question. "
          "I answer only from the documents, so I won't guess.",
}
REFUSED = {
    "ro": "Pot răspunde doar la întrebări despre Primăria Chișinău, serviciile și documentele ei.",
    "ru": "Я отвечаю только на вопросы о Примэрии Кишинэу, её услугах и документах.",
    "en": "I can only answer questions about the Chișinău City Hall, its services and documents.",
}
# Greetings, thanks and "who are you": answered by code, without a search (nothing to cite, nothing to show searched).
SMALL_TALK = re.compile(
    r"^\W*(?:(?:salut\w*|bun[aă](?: ziua| seara| dimineața| dimineata)?|noroc|hei|hello|hi|hey|"
    r"привет\w*|здравствуй\w*|добр\w+ (?:день|утро|вечер)|добрый|хай|"
    r"mul[țt]umesc\w*|mersi|merci|спасибо|благодарю|thanks?(?: you)?|"
    r"la revedere|pa|пока|до свидания|bye|"
    r"ce faci|ce mai faci|как дела|как ты|"
    r"cine e[șs]ti|cine sunte[țt]i|ce po[țt]i(?: face)?|кто ты|кто вы|что ты умеешь|что умеешь|что вы умеете)"
    r"[\s!.,?)(]*)+$", re.I)
SMALL_TALK_ANSWER = {
    "ro": "Bună! Sunt asistentul Primăriei Chișinău. Întrebați-mă despre serviciile, deciziile și documentele "
          "publice ale Primăriei, iar eu răspund cu trimitere la documentul și pasajul exact.",
    "ru": "Здравствуйте! Я ассистент Примэрии Кишинэу. Спросите меня об услугах, решениях и публичных документах "
          "Примэрии, и я отвечу со ссылкой на конкретный документ и фрагмент.",
    "en": "Hello! I'm the Chișinău City Hall assistant. Ask me about the City Hall's services, decisions and public "
          "documents, and I'll answer with a reference to the exact document and passage.",
}
SEARCH_SUMMARY = {
    "ro": "Găsite {chunks} fragmente în {docs} documente",
    "ru": "Найдено фрагментов: {chunks}, документов: {docs}",
    "en": "Found {chunks} passages in {docs} documents",
}
VERIFY_SUMMARY = {
    "ro": "Citate confirmate: {ok} din {total} propoziții",
    "ru": "Подтверждено цитатами: {ok} из {total} предложений",
    "en": "Backed by quotes: {ok} of {total} sentences",
}
SOURCE_PAGE = {"ro": "Pagina sursei pe {site}", "ru": "Страница источника на {site}", "en": "Source page on {site}"}
FRESH_SUMMARY = {"ro": "Caut acte mai noi… găsite {n}", "ru": "Ищу более новые документы… найдено {n}",
                 "en": "Looking for newer acts… found {n}"}
NO_ANSWER_CONTACTS = {
    "ro": "Din păcate nu putem răspunde la această întrebare din documentele disponibile. "
          "Credem că vă poate ajuta: {names}.",
    "ru": "К сожалению, мы не можем ответить на этот вопрос по имеющимся документам. Думаем, вам поможет: {names}.",
    "en": "Unfortunately we can't answer this question from the available documents. We think these can help: {names}.",
}
PARTIAL_CONTACTS = {"ro": "Pentru ce lipsește din documente, credem că vă poate ajuta: {names}.",
                    "ru": "По тому, чего нет в документах, думаем, вам поможет: {names}.",
                    "en": "For what the documents don't cover, we think these can help: {names}."}
CONTACT_REASON = {"ro": "Pagina lor de pe {site} este cea mai apropiată de întrebarea dvs.",
                  "ru": "Их страница на {site} ближе всего к вашему вопросу.",
                  "en": "Their page on {site} is the closest to your question."}
GENERAL_REASON = {"ro": "Contactul general al Primăriei municipiului Chișinău.",
                  "ru": "Общий контакт Примэрии муниципия Кишинэу.",
                  "en": "The general contact of the Chișinău City Hall."}
