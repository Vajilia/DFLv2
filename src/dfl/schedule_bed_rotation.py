"""DFL approved eight-year division-bed permutation.

The workbook's eight beds are positional slots A..H. Division identity and
franchise seeds are independent. In years 1..4 rotate each four-bed conference
block right one bed annually; on years 5..8 swap complete four-division blocks
and continue the internal rotation. Repeat every eight years.
"""


def divisions_by_bed(year: int, divisions: tuple = tuple(range(1, 9))) -> tuple:
    """Return division assigned to beds A..H for a positive, 1-based year."""
    if type(year) is not int or year < 1:
        raise ValueError('year must be a positive integer')
    if len(divisions) != 8 or len(set(divisions)) != 8:
        raise ValueError('eight unique divisions required in founding bed order')
    offset = (year - 1) % 4
    swapped = ((year - 1) // 4) % 2
    return tuple(divisions[((bed // 4) ^ swapped) * 4 + (bed % 4 - offset) % 4]
                 for bed in range(8))


def bed_of_division(year: int, division, divisions: tuple = tuple(range(1, 9))) -> int:
    """Zero-based workbook bed index for a division in the given season."""
    try:
        return divisions_by_bed(year, divisions).index(division)
    except ValueError as exc:
        raise ValueError('unknown division') from exc
