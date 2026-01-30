"""Icelandic language utilities using BÍN (Beygingarlýsing íslensks nútímamáls)."""

from functools import lru_cache

from islenska import Bin

_bin = Bin()


@lru_cache(maxsize=1000)
def get_word_forms(word: str) -> set[str]:
    """Get all inflected forms of an Icelandic word from BÍN.

    Args:
        word: The word to look up (any inflected form works)

    Returns:
        Set of all inflected forms, including the original word.
        Returns just the original word if not found in BÍN.
    """
    word = word.strip().lower()
    if not word:
        return set()

    forms = {word}  # Always include the original

    try:
        result = _bin.lookup(word)
        if result and result[1]:
            # Get the BIN ID from the first matching entry
            bin_id = result[1][0].bin_id

            # Look up all forms with this ID
            all_forms = _bin.lookup_id(bin_id)
            for form in all_forms:
                forms.add(form.bmynd.lower())
    except Exception:
        pass  # If BÍN lookup fails, just use the original word

    return forms


def get_all_query_forms(query: str) -> dict[str, set[str]]:
    """Get all inflected forms for each word in a query.

    Args:
        query: Search query (can be multiple words)

    Returns:
        Dict mapping each original word to its set of inflected forms
    """
    words = query.split()
    return {word: get_word_forms(word) for word in words if len(word) >= 2}
