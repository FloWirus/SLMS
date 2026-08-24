from music_sync.gui.models import format_size, _numeric_sort_string, polish_sort_key


def test_polish_letters_sort_after_their_base_letter():
    names = ["Zebra", "Łódź", "Lampa", "Ćma", "Cegła", "Ala"]
    assert sorted(names, key=polish_sort_key) == ["Ala", "Cegła", "Ćma", "Lampa", "Łódź", "Zebra"]


def test_polish_sort_key_is_case_insensitive():
    assert polish_sort_key("abc") == polish_sort_key("ABC")


def test_numeric_sort_string_orders_numerically_not_lexically():
    values = ["10", "9", "100", "1"]
    assert sorted(values, key=_numeric_sort_string) == ["1", "9", "10", "100"]


def test_numeric_sort_string_puts_blanks_last():
    values = ["2", "", "1", "not a number"]
    ordered = sorted(values, key=_numeric_sort_string)
    assert ordered[:2] == ["1", "2"]
    assert set(ordered[2:]) == {"", "not a number"}


def test_numeric_sort_string_keys_are_comparable_strings():
    # They feed Qt.UserRole, which compares through QVariant and can not
    # compare tuples or floats-with-flags -- hence fixed-width strings.
    assert isinstance(_numeric_sort_string("1"), str)
    assert len(_numeric_sort_string("1")) == len(_numeric_sort_string("123456"))


def test_format_size():
    assert format_size(0) == "0.0 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"
