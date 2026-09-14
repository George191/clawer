
import re


def parse_warning_no(
    warning_no: str | None,
) -> tuple[int | None, int | None, str | None]:
    if not warning_no:
        return None, None, None

    match = re.fullmatch(
        r"\s*(?P<left>\d+)\s*(?P<separator>[/\-])\s*"
        r"(?P<right>\d+)\s*"
        r"(?:\((?P<subregion>[^()]*)\))?\s*",
        warning_no,
    )
    if not match:
        return None, None, None

    left = match.group("left")
    right = match.group("right")
    separator = match.group("separator")

    # 496/26：序号/年份
    # 21-0325：年份-序号
    if separator == "-" and len(left) == 2 and len(right) == 4:
        year_text = left
        serial_text = right
    else:
        serial_text = left
        year_text = right

    serial_number = int(serial_text)

    if len(year_text) == 2:
        warning_year = 2000 + int(year_text)
    else:
        warning_year = int(year_text)

    subregion = match.group("subregion")
    subregion = subregion.strip() if subregion else None

    return serial_number, warning_year, subregion

if __name__ == "__main__":
    warning_no = "496/26(24)"
    serial_number, warning_year, subregion = parse_warning_no(warning_no)
    print(serial_number, warning_year, subregion)


    warning_no = "21-0325(24)"
    serial_number, warning_year, subregion = parse_warning_no(warning_no)
    print(serial_number, warning_year, subregion)


    warning_no = "21-0325"
    serial_number, warning_year, subregion = parse_warning_no(warning_no)
    print(serial_number, warning_year, subregion)


    warning_no = "496/26"
    serial_number, warning_year, subregion = parse_warning_no(warning_no)
    print(serial_number, warning_year, subregion)