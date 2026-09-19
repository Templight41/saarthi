"""The languages Saarthi works in. One list, because there used to be three.

Two capabilities, and they are not the same set. Sarvam's speech-to-text
understands two dozen Indian languages; bulbul speaks eleven of them. So a
merchant can be *understood* in Maithili and written to in Maithili, and
cannot be spoken back to in it.

That gap is modelled rather than papered over. Falling back to an English
voice for an unspeakable language would have bulbul read Devanagari or Ol
Chiki with an English reader, which is worse than staying quiet — so
`is_speakable` gates the audio and the merchant still gets the text.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Let the provider work it out from the audio.
AUTO = "unknown"
DEFAULT_LANGUAGE = "en-IN"


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    #: Native name, for a selector a merchant can actually read.
    endonym: str
    #: bulbul can say it. The transcriber understands every entry here.
    speakable: bool

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "endonym": self.endonym,
            "speakable": self.speakable,
        }


LANGUAGES: tuple[Language, ...] = (
    Language("en-IN", "Indian English", "English", True),
    Language("hi-IN", "Hindi", "हिन्दी", True),
    Language("bn-IN", "Bengali", "বাংলা", True),
    Language("gu-IN", "Gujarati", "ગુજરાતી", True),
    Language("kn-IN", "Kannada", "ಕನ್ನಡ", True),
    Language("ml-IN", "Malayalam", "മലയാളം", True),
    Language("mr-IN", "Marathi", "मराठी", True),
    Language("od-IN", "Odia", "ଓଡ଼ିଆ", True),
    Language("pa-IN", "Punjabi", "ਪੰਜਾਬੀ", True),
    Language("ta-IN", "Tamil", "தமிழ்", True),
    Language("te-IN", "Telugu", "తెలుగు", True),
    # Understood and written, not spoken.
    Language("as-IN", "Assamese", "অসমীয়া", False),
    Language("ur-IN", "Urdu", "اردو", False),
    Language("ne-IN", "Nepali", "नेपाली", False),
    Language("kok-IN", "Konkani", "कोंकणी", False),
    Language("ks-IN", "Kashmiri", "کٲشُر", False),
    Language("sd-IN", "Sindhi", "سنڌي", False),
    Language("sa-IN", "Sanskrit", "संस्कृतम्", False),
    Language("sat-IN", "Santali", "ᱥᱟᱱᱛᱟᱲᱤ", False),
    Language("mni-IN", "Manipuri", "ꯃꯤꯇꯩꯂꯣꯟ", False),
    Language("brx-IN", "Bodo", "बर'", False),
    Language("mai-IN", "Maithili", "मैथिली", False),
    Language("doi-IN", "Dogri", "डोगरी", False),
)

BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}
#: Everything the transcriber understands.
UNDERSTOOD = frozenset(BY_CODE)
#: The subset bulbul can say out loud.
SPEAKABLE = frozenset(code for code, lang in BY_CODE.items() if lang.speakable)


def is_understood(code: str | None) -> bool:
    return bool(code) and code in UNDERSTOOD


def is_speakable(code: str | None) -> bool:
    return bool(code) and code in SPEAKABLE


def name_of(code: str | None) -> str:
    language = BY_CODE.get(code or "")
    return language.name if language else (code or DEFAULT_LANGUAGE)


def normalise(code: str | None, *, default: str = DEFAULT_LANGUAGE) -> str:
    """A language we understand, or the default. Never raises."""
    return code if is_understood(code) else default


def for_transcription(code: str | None) -> str:
    """What to ask the transcriber for. Blank or unknown means auto-detect."""
    if not code or code == AUTO:
        return AUTO
    return code if is_understood(code) else AUTO


def catalogue() -> list[dict]:
    return [lang.as_dict() for lang in LANGUAGES]
