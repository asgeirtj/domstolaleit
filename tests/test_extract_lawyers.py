"""Tests for lawyer extraction and outcome determination."""

import pytest

from scripts.extract_lawyers import (
    determine_outcome_civil,
    extract_lawyers_from_verdict,
)


class TestDetermineOutcomeCivil:
    """Tests for civil case outcome determination."""

    def test_defendant_acquitted_simple(self):
        """Basic 'stefndi er sýkn' pattern."""
        pl, df = determine_outcome_civil("Stefndi er sýkn af kröfum stefnanda.")
        assert (pl, df) == ("loss", "win")

    def test_defendant_acquitted_with_newline_and_entity_name(self):
        """E-95/2016: newline between entity name and 'er sýkn' must still match.

        The Domsord was:
            Stefndi, Knattspyrnufelagid Haukar,
            er sykn i mali thessu.

        The '.' in regex doesn't match newlines by default, so the pattern
        'stefnd\\w*.{0,120}?sykn' failed when a newline appeared between
        the defendant name and 'er sykn'.
        """
        domsord = (
            "Stefndi, Knattspyrnufélagið Haukar,\n"
            "er sýkn í máli þessu.\n"
            "Málskostnaður fellur niður."
        )
        pl, df = determine_outcome_civil(domsord)
        assert (pl, df) == ("loss", "win")

    def test_defendant_acquitted_multiline_long_name(self):
        """Entity name spanning multiple lines before 'er sýkn'."""
        domsord = (
            "Stefndi, Tryggingamiðstöðin hf.,\n"
            "er sýkn af öllum kröfum stefnanda í máli þessu."
        )
        pl, df = determine_outcome_civil(domsord)
        assert (pl, df) == ("loss", "win")

    def test_plaintiff_wins_payment(self):
        """Basic plaintiff wins: defendant ordered to pay."""
        domsord = "Stefndi greiði stefnanda 5.000.000 króna."
        pl, df = determine_outcome_civil(domsord)
        assert (pl, df) == ("win", "loss")

    def test_case_dismissed(self):
        """Case dismissed = defendant wins."""
        domsord = "Málinu er vísað frá dómi."
        pl, df = determine_outcome_civil(domsord)
        assert (pl, df) == ("loss", "win")
