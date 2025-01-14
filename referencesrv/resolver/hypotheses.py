import re

from referencesrv.resolver.common import Hypothesis
from referencesrv.resolver.authors import normalize_author_list, get_first_author_last_name
from referencesrv.resolver.scoring import get_score_for_reference_identifier, get_thesis_score_for_input_fields, \
    get_serial_score_for_input_fields, get_book_score_for_input_fields
from referencesrv.resolver.specialrules import iter_journal_specific_hypotheses
from referencesrv.resolver.journalfield import get_best_bibstem_for, cook_title_string, has_thesis_indicators

from flask import current_app


class Hypotheses(object):
    # Mapping of standard keys to our internal input_field keys.
    field_mappings = [
        ("author", "authors"),
        ("pub", "journal"),
        ("pub", "book"),
        ("volume", "volume"),
        ("issue", "issue"),
        ("page", "page"),
        ("year", "year"),
        ("title", "title"),
        ("refstr", "refstr"),
        ("doi", "doi"),
        ("arxiv", "arxiv"),
    ]

    ETAL_PAT = re.compile(r"((?i)[\s,]*et\.?\s*al\.?)")
    JOURNAL_LETTER_ATTACHED_VOLUME = re.compile(r"^([ABCDEFGIT])\d+$")

    def __init__(self, ref):
        """
        
        :param ref: 
        """
        self.ref = ref
        self.make_digested_record()

    def make_digested_record(self):
        """
        adds a digested_record attribute from field_mappings and self.ref.

        This is exclusively called by the constructor.
        :return:
        """
        self.digested_record = {}
        for dest_key, src_key in self.field_mappings:
            value = self.ref.get(src_key)
            if value:
                self.digested_record[dest_key] = value

        self.normalized_authors = None
        if "author" in self.digested_record:
            self.digested_record["author"] = self.ETAL_PAT.sub('', self.digested_record["author"])
            self.normalized_authors = normalize_author_list(self.digested_record["author"], initials=True)
            self.normalized_first_author =  re.sub(r"\.( ?[A-Z]\.)*", "", re.sub("-[A-Z]\.", "", self.normalized_authors)).split(";")[0].strip()

        if "year" in self.digested_record and len(self.digested_record["year"]) > 4:
            # the extra character(s) are at the end, just to be smart about it let's go with RE
            self.digested_record["year"] = re.findall(r'^([12][089]\d\d)', self.digested_record["year"])[0]

        if "-" in self.digested_record.get("page", ""):
            # we are querying on page stat, for now through out the page end
            self.digested_record["page"] = self.digested_record["page"].split("-")[0]

        if "volume" in self.digested_record and "pub" in self.digested_record:
            # if volume has a alpha character at the beginning, remove it and attach it to the journal
            # ie. A. Arvanitaki, S. Dimopoulos, S. Dubovsky, N. Kaloper, and J. March-Russell, "String Axiverse," "Phys. Rev.", vol. D81, p. 123530, 2010.
            # which is in fact Journal `Phys. Rev. D.` Volume `81`
            match = self.JOURNAL_LETTER_ATTACHED_VOLUME.match(self.digested_record["volume"])
            if match:
                self.digested_record["pub"] = '%s %s'%(self.digested_record["pub"], self.digested_record["volume"][0])
                self.digested_record["volume"] = self.digested_record["volume"][1:]

    def has_keys(self, *keys):
        """
        returns True if the digested record has at least all the fields in keys.

        :param keys:
        :return:
        """
        for key in keys:
            if not self.digested_record.get(key):
                return False
        return True

    def lacks_keys(self, *keys):
        """

        :param keys:
        :return:
        """
        for key in keys:
            if self.digested_record.get(key):
                return False
        return True

    def construct_bibcode(self):
        """
        BIBCODE_FIELDS = [
            ('year', 0, 4, 'r', int),
            ('journal', 4, 9, 'l', str),
            ('volume', 9, 13, 'r', str),
            ('qualifier', 13, 14, 'r', str),
            ('page', 14, 18, 'r', str),
            ('initial', 18, 19, 'r', str)
        ]
        :return:
        """
        year = self.digested_record["year"]
        journal = get_best_bibstem_for(self.digested_record["pub"])
        journal = journal + (5-len(journal)) * '.'
        volume = self.digested_record.get("volume", "")
        volume = (4 - len(volume)) * '.' + volume
        page_qualifier = self.digested_record.get("qualifier", ".")
        page = self.digested_record.get("page", "")[:4]
        page = (4 - len(page)) * '.' + page
        initial = self.normalized_authors[0] if self.normalized_authors else '.'
        self.digested_record["bibcode"] = '{year}{journal}{volume}{page_qualifier}{page}{initial}'.format(
                                            year=year,journal=journal,volume=volume,page_qualifier=page_qualifier,page=page,initial=initial)
        return self.digested_record["bibcode"]

    def iter_hypotheses(self):
        match = self.ETAL_PAT.search(str(self.ref))
        has_etal = match is not None

        # has_etal = self.ETAL_PAT.search(
        #     self.digested_record.get("author", ""))

        # If there's a DOI, use it.
        if self.has_keys("doi"):
            yield Hypothesis("fielded-DOI", {
                    "doi": self.digested_record["doi"]},
                get_score_for_reference_identifier,
                input_fields=self.digested_record)

        # If there's a arxiv id, use it.
        if self.has_keys("arxiv"):
            yield Hypothesis("fielded-arxiv", {
                    "arxiv": self.digested_record["arxiv"]},
                get_score_for_reference_identifier,
                input_fields=self.digested_record)

        # try the old way, construct bibcode
        if self.has_keys("author", "year", "pub"):
            self.construct_bibcode()
            yield Hypothesis("fielded-bibcode", {
                    "bibcode": self.digested_record["bibcode"]},
                get_score_for_reference_identifier,
                input_fields=self.digested_record)

        # try author, year, pub, volume, and page
        if self.has_keys("author", "year", "volume", "page"):
            yield Hypothesis("fielded-auth/year/volume/page", {
                "author": self.normalized_authors,
                "year": self.digested_record["year"],
                "volume": self.digested_record["volume"],
                "page": self.digested_record["page"]},
                             get_serial_score_for_input_fields,
                             input_fields=self.digested_record,
                             page_qualifier=self.digested_record.get("qualifier", ""),
                             has_etal=has_etal,
                             normalized_authors=self.normalized_authors)

        # search by author, bibstem, and year
        if self.has_keys("author", "pub", "year"):
            yield Hypothesis("fielded-auth/pub/year", {
                    "author": self.normalized_authors,
                    "bibstem": get_best_bibstem_for(self.digested_record["pub"]),
                    "year": self.digested_record["year"]},
                get_serial_score_for_input_fields,
                input_fields=self.digested_record,
                page_qualifier=self.digested_record.get("qualifier", ""),
                has_etal=has_etal,
                normalized_authors=self.normalized_authors)

        # pull out titles
        if self.has_keys("author", "year", "title"):
            yield Hypothesis("fielded-title", {
                "first_author_norm": self.normalized_first_author,
                "year": self.digested_record["year"],
                "title": self.digested_record["title"],},
            get_serial_score_for_input_fields,
            input_fields=self.digested_record,
            page_qualifier='',
            has_etal=False,
            normalized_authors='')

        # try resolving as book, is title in the pub
        if self.has_keys("author", "pub", "year") and self.lacks_keys("title"):
            cleaned_title = cook_title_string(self.digested_record["pub"])
            # if what's left the the title is too short, revert the cleanup.
            if len(cleaned_title)<15:
                cleaned_title = self.digested_record["pub"]

            yield Hypothesis("fielded-book", {
                   "author": self.normalized_authors,
                    "title": cleaned_title,
                    "year": self.digested_record["year"]},
                get_book_score_for_input_fields,
                input_fields=self.digested_record,
                page_qualifier=self.digested_record.get("qualifier", ""),
                has_etal=False,
                normalized_authors=self.normalized_authors)

        # could this be a thesis?
        if self.has_keys("author", "year", "refstr") and self.lacks_keys("volume", "page"):
            # we're checking if any thesis indicators are in pub
            # and later pass on all thesis indicators to solr since we're
            # not sure if the ref thesis words have anything to do with
            # what the ADS thesis words are, plus we don't want any stopwords
            # or other junk in a disjunction, so just oring the words from
            # pub together is not a good idea either.
            if has_thesis_indicators(self.digested_record["refstr"]):
                yield Hypothesis("fielded-thesis", {
                    "author": self.normalized_authors,
                    "pub_escaped": "(%s)"%" or ".join(current_app.config["THESIS_INDICATOR_WORDS"]),
                    "year": self.digested_record["year"]},
                get_thesis_score_for_input_fields,
                input_fields=self.digested_record,
                normalized_authors=self.normalized_authors)

        # try some reference type-specific hypotheses
        if "pub" in self.digested_record:
            self.digested_record["bibstem"] = get_best_bibstem_for(self.digested_record["pub"])
            for hypo in iter_journal_specific_hypotheses(
                    self.digested_record.get("bibstem"),
                    self.digested_record.get("year"),
                    self.normalized_authors,
                    self.digested_record.get("pub"),
                    self.digested_record.get("volume"),
                    self.digested_record.get("page"),
                    self.digested_record.get("refstr")):
                yield hypo

        # if we have sufficient entropy in the page, it might be good for
        # a hypothesis
        if self.has_keys("author") and len(self.digested_record.get("page", ""))>2:
            yield Hypothesis("fielded-author/page", {
                "author": self.normalized_authors,
                "page": self.digested_record["page"].split("-")[0]},
                get_serial_score_for_input_fields,
                input_fields=self.digested_record)

        # now try query on first author norm and year only.
        if self.has_keys("author", "year"):
            yield Hypothesis("fielded-first-author-norm/year", {
                "first_author_norm": self.normalized_first_author,
                "year": self.digested_record["year"]},
                get_serial_score_for_input_fields,
                input_fields=self.digested_record,
                page_qualifier=self.digested_record.get("qualifier", ""),
                has_etal=has_etal,
                normalized_authors=self.normalized_authors)

        # now try query on approximate first author norm and year only.
        if self.has_keys("author", "year"):
            yield Hypothesis("fielded-first-author-norm~/year", {
                "first_author_norm~": self.normalized_first_author,
                "year": self.digested_record["year"]},
                get_serial_score_for_input_fields,
                input_fields=self.digested_record,
                page_qualifier=self.digested_record.get("qualifier", ""),
                has_etal=has_etal,
                normalized_authors=self.normalized_authors)

        # now try query on authors and approximate year only.
        if self.has_keys("author", "year"):
            yield Hypothesis("fielded-author-norm/year~", {
                "author": self.normalized_authors,
                "year~": self.digested_record["year"]},
                get_serial_score_for_input_fields,
                input_fields=self.digested_record,
                page_qualifier=self.digested_record.get("qualifier", ""),
                has_etal=has_etal,
                normalized_authors=self.normalized_authors)

        # if no author!
        # try bibstem-year-volume-page
        if self.has_keys("year", "pub", "volume", "page"):
            yield Hypothesis("fielded-no-author", {
                    "bibstem": get_best_bibstem_for(self.digested_record["pub"]),
                    "year": self.digested_record["year"],
                    "volume": self.digested_record["volume"],
                    "page": self.digested_record.get("qualifier", "")+self.digested_record["page"]},
                get_serial_score_for_input_fields,
                input_fields=self.digested_record,
                page_qualifier='',
                has_etal=False,
                normalized_authors='')

        # if no year!
        if self.has_keys("author", "pub", "volume", "page"):
            yield Hypothesis("fielded-no-year", {
                "author": self.normalized_authors,
                "bibstem": get_best_bibstem_for(self.digested_record["pub"]),
                "volume": self.digested_record["volume"],
                "page": self.digested_record["page"]},
                             get_serial_score_for_input_fields,
                             input_fields=self.digested_record,
                             page_qualifier=self.digested_record.get("qualifier", ""),
                             has_etal=has_etal,
                             normalized_authors=self.normalized_authors)

