import sys

# This can only be tested by using different python versions, therefore it is not covered by coverage.py
if sys.version_info.major == 2: # pragma: no cover
    reload(sys)
    sys.setdefaultencoding('utf-8')

import json

from xml.etree import ElementTree

import re

from ._html_unescaping import unescape
from ._errors import VideoUnavailable, NoTranscriptFound, TranscriptsDisabled
from ._settings import WATCH_URL


class TranscriptListFetcher():
    def __init__(self, http_client):
        self._http_client = http_client

    def fetch(self, video_id):
        return TranscriptList.build(
            self._http_client,
            video_id,
            self._extract_captions_json(self._fetch_html(video_id), video_id)
        )

    def _extract_captions_json(self, html, video_id):
        splitted_html = html.split('"captions":')

        if len(splitted_html) <= 1:
            if '"playabilityStatus":' not in html:
                raise VideoUnavailable(video_id)

            raise TranscriptsDisabled(video_id)

        return json.loads(splitted_html[1].split(',"videoDetails')[0].replace('\n', ''))[
            'playerCaptionsTracklistRenderer'
        ]

    def _fetch_html(self, video_id):
        return self._http_client.get(WATCH_URL.format(video_id=video_id)).text.replace(
            '\\u0026', '&'
        ).replace(
            '\\', ''
        )


class TranscriptList():
    """
    This object represents a list of transcripts. It can be iterated over to list all transcripts which are available
    for a given YouTube video. Also it provides functionality to search for a transcript in a given language.
    """

    # TODO implement iterator

    def __init__(self, video_id, manually_created_transcripts, generated_transcripts):
        """
        The constructor is only for internal use. Use the static build method instead.

        :param video_id: the id of the video this TranscriptList is for
        :type video_id: str
        :param manually_created_transcripts: dict mapping language codes to the manually created transcripts
        :type manually_created_transcripts: dict[str, Transcript]
        :param generated_transcripts: dict mapping language codes to the generated transcripts
        :type generated_transcripts: dict[str, Transcript]
        """
        self.video_id = video_id
        self._manually_created_transcripts = manually_created_transcripts
        self._generated_transcripts = generated_transcripts

    @staticmethod
    def build(http_client, video_id, captions_json):
        """
        Factory method for TranscriptList.

        :param http_client: http client which is used to make the transcript retrieving http calls
        :type http_client: requests.Session
        :param video_id: the id of the video this TranscriptList is for
        :type video_id: str
        :param captions_json: the JSON parsed from the YouTube pages static HTML
        :type captions_json: dict
        :return: the created TranscriptList
        :rtype TranscriptList
        """
        translation_languages = [
            {
                'language': translation_language['languageName']['simpleText'],
                'language_code': translation_language['languageCode'],
            } for translation_language in captions_json['translationLanguages']
        ]

        manually_created_transcripts = {}
        generated_transcripts = {}

        for caption in captions_json['captionTracks']:
            if caption.get('kind', '') == 'asr':
                transcript_dict = generated_transcripts
            else:
                transcript_dict = manually_created_transcripts

            transcript_dict[caption['languageCode']] = Transcript(
                http_client,
                video_id,
                caption['baseUrl'],
                caption['name']['simpleText'],
                caption['languageCode'],
                caption.get('kind', '') == 'asr',
                translation_languages if caption['isTranslatable'] else []
            )

        return TranscriptList(
            video_id,
            manually_created_transcripts,
            generated_transcripts,
        )

    def find_transcript(self, language_codes):
        """
        Finds a transcript for a given language code. Manually created transcripts are returned first and only if none
        are found, generated transcripts are used. If you only want generated transcripts use
        find_manually_created_transcript instead.

        :param language_codes: A list of language codes in a descending priority. For example, if this is set to
        ['de', 'en'] it will first try to fetch the german transcript (de) and then fetch the english transcript (en) if
        it fails to do so.
        :type languages: [str]
        :return: the found Transcript
        :rtype: Transcript
        :raises: NoTranscriptFound
        """
        return self._find_transcript(language_codes, [self._manually_created_transcripts, self._generated_transcripts])

    def find_generated_transcript(self, language_codes):
        """
        Finds a automatically generated transcript for a given language code.

        :param language_codes: A list of language codes in a descending priority. For example, if this is set to
        ['de', 'en'] it will first try to fetch the german transcript (de) and then fetch the english transcript (en) if
        it fails to do so.
        :type languages: [str]
        :return: the found Transcript
        :rtype: Transcript
        :raises: NoTranscriptFound
        """
        return self._find_transcript(language_codes, [self._generated_transcripts,])

    def find_manually_created_transcript(self, language_codes):
        """
        Finds a manually created transcript for a given language code.

        :param language_codes: A list of language codes in a descending priority. For example, if this is set to
        ['de', 'en'] it will first try to fetch the german transcript (de) and then fetch the english transcript (en) if
        it fails to do so.
        :type languages: [str]
        :return: the found Transcript
        :rtype: Transcript
        :raises: NoTranscriptFound
        """
        return self._find_transcript(language_codes, [self._manually_created_transcripts,])

    def _find_transcript(self, language_codes, transcript_dicts):
        for language_code in language_codes:
            for transcript_dict in transcript_dicts:
                if language_code in transcript_dict:
                    return transcript_dict[language_code]

        raise NoTranscriptFound(
            self.video_id,
            language_codes,
            self
        )

    def __str__(self):
        return (
            'For this video ({video_id}) transcripts are available in the following languages:\n\n'
            '(MANUALLY CREATED)\n'
            '{available_manually_created_transcript_languages}\n\n'
            '(GENERATED)\n'
            '{available_generated_transcripts}'
        ).format(
            video_id=self.video_id,
            available_manually_created_transcript_languages=self._get_language_description(
                self._manually_created_transcripts.values()
            ),
            available_generated_transcripts=self._get_language_description(
                self._generated_transcripts.values()
            ),
        )

    def _get_language_description(self, transcripts):
        return '\n'.join(
            ' - {transcript}'.format(transcript=str(transcript))
            for transcript in transcripts
        ) if transcripts else 'None'


class Transcript():
    def __init__(self, http_client, video_id, url, language, language_code, is_generated, translation_languages):
        """
        You probably don't want to initialize this directly. Usually you'll access Transcript objects using a
        TranscriptList.

        :param http_client: http client which is used to make the transcript retrieving http calls
        :type http_client: requests.Session
        :param video_id: the id of the video this TranscriptList is for
        :type video_id: str
        :param url: the url which needs to be called to fetch the transcript
        :param language: the name of the language this transcript uses
        :param language_code:
        :param is_generated:
        :param translation_languages:
        """
        self._http_client = http_client
        self.video_id = video_id
        self._url = url
        self.language = language
        self.language_code = language_code
        self.is_generated = is_generated
        self.translation_languages = translation_languages

    def fetch(self):
        """
        Loads the actual transcript data.

        :return: a list of dictionaries containing the 'text', 'start' and 'duration' keys
        :rtype: [{'text': str, 'start': float, 'end': float}]
        """
        return _TranscriptParser().parse(
            self._http_client.get(self._url).text
        )

    def __str__(self):
        return '{language_code} ("{language}")'.format(
            language=self.language,
            language_code=self.language_code,
        )

# TODO integrate translations in future release
#     @property
#     def is_translatable(self):
#         return len(self.translation_languages) > 0
#
#
# class TranslatableTranscript(Transcript):
#     def __init__(self, http_client, url, translation_languages):
#         super(TranslatableTranscript, self).__init__(http_client, url)
#         self._translation_languages = translation_languages
#         self._translation_language_codes = {language['language_code'] for language in translation_languages}
#
#
#     def translate(self, language_code):
#         if language_code not in self._translation_language_codes:
#             raise TranslatableTranscript.TranslationLanguageNotAvailable()
#
#         return Transcript(
#             self._http_client,
#             '{url}&tlang={language_code}'.format(url=self._url, language_code=language_code)
#         )


class _TranscriptParser():
    HTML_TAG_REGEX = re.compile(r'<[^>]*>', re.IGNORECASE)

    def parse(self, plain_data):
        return [
            {
                'text': re.sub(self.HTML_TAG_REGEX, '', unescape(xml_element.text)),
                'start': float(xml_element.attrib['start']),
                'duration': float(xml_element.attrib['dur']),
            }
            for xml_element in ElementTree.fromstring(plain_data)
            if xml_element.text is not None
        ]
