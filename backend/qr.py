"""Dependency-free QR generation adapted from Project Nayuki."""

def _qr_gf_multiply(left: int, right: int) -> int:
    result = 0
    for bit in range(7, -1, -1):
        result = (result << 1) ^ ((result >> 7) * 0x11D)
        result ^= ((left >> bit) & 1) * right
    return result


def _qr_reed_solomon(data: list[int], degree: int) -> list[int]:
    divisor = [0] * degree
    divisor[-1] = 1
    root = 1
    for _ in range(degree):
        for index in range(degree):
            divisor[index] = _qr_gf_multiply(divisor[index], root)
            if index + 1 < degree:
                divisor[index] ^= divisor[index + 1]
        root = _qr_gf_multiply(root, 2)
    remainder = [0] * degree
    for value in data:
        factor = value ^ remainder.pop(0)
        remainder.append(0)
        for index, coefficient in enumerate(divisor):
            remainder[index] ^= _qr_gf_multiply(coefficient, factor)
    return remainder


def _qr_mask(mask: int, row: int, column: int) -> bool:
    if mask == 0:
        return (row + column) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return column % 3 == 0
    if mask == 3:
        return (row + column) % 3 == 0
    if mask == 4:
        return (row // 2 + column // 3) % 2 == 0
    if mask == 5:
        return (row * column) % 2 + (row * column) % 3 == 0
    if mask == 6:
        return ((row * column) % 2 + (row * column) % 3) % 2 == 0
    return ((row + column) % 2 + (row * column) % 3) % 2 == 0


def _qr_format_bits(mask: int) -> int:
    data = (1 << 3) | mask  # Error correction level L is binary 01.
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _qr_draw_format(modules: list[list[bool]], mask: int) -> None:
    size = len(modules)
    bits = _qr_format_bits(mask)
    for index in range(6):
        modules[index][8] = bool((bits >> index) & 1)
    modules[7][8] = bool((bits >> 6) & 1)
    modules[8][8] = bool((bits >> 7) & 1)
    modules[8][7] = bool((bits >> 8) & 1)
    for index in range(9, 15):
        modules[8][14 - index] = bool((bits >> index) & 1)
    for index in range(8):
        modules[8][size - 1 - index] = bool((bits >> index) & 1)
    for index in range(8, 15):
        modules[size - 15 + index][8] = bool((bits >> index) & 1)
    modules[size - 8][8] = True


def _qr_penalty(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0
    for lines in (modules, [[modules[row][column] for row in range(size)] for column in range(size)]):
        for line in lines:
            run_color = line[0]
            run_length = 1
            for value in line[1:]:
                if value == run_color:
                    run_length += 1
                else:
                    if run_length >= 5:
                        score += 3 + run_length - 5
                    run_color = value
                    run_length = 1
            if run_length >= 5:
                score += 3 + run_length - 5
            pattern = "".join("1" if value else "0" for value in line)
            score += 40 * (pattern.count("00001011101") + pattern.count("10111010000"))
    for row in range(size - 1):
        for column in range(size - 1):
            value = modules[row][column]
            if all(modules[row + dy][column + dx] == value for dy in (0, 1) for dx in (0, 1)):
                score += 3
    dark = sum(value for row in modules for value in row)
    score += abs(dark * 20 - size * size * 10) // (size * size) * 10
    return score


def _qr_matrix(text: str) -> list[str]:
    """Create a fixed version-5-L QR matrix for a short local transfer URL."""
    payload = text.encode("utf-8")
    if len(payload) > 106:
        raise ValueError("Transfer URL is too long for the QR code")
    bits: list[int] = [0, 1, 0, 0]
    bits.extend((len(payload) >> shift) & 1 for shift in range(7, -1, -1))
    for value in payload:
        bits.extend((value >> shift) & 1 for shift in range(7, -1, -1))
    bits.extend([0] * min(4, 864 - len(bits)))
    bits.extend([0] * ((8 - len(bits) % 8) % 8))
    data = [sum(bits[offset + bit] << (7 - bit) for bit in range(8)) for offset in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(data) < 108:
        data.append(pad[(len(data) - (len(bits) // 8)) % 2])
    codewords = data + _qr_reed_solomon(data, 26)
    code_bits = [(value >> shift) & 1 for value in codewords for shift in range(7, -1, -1)]

    size = 37
    base = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def set_function(row: int, column: int, value: bool) -> None:
        if 0 <= row < size and 0 <= column < size:
            base[row][column] = value
            function[row][column] = True

    for center_row, center_column in ((3, 3), (3, size - 4), (size - 4, 3)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                set_function(center_row + dy, center_column + dx, distance not in (2, 4))
    for index in range(size):
        if not function[6][index]:
            set_function(6, index, index % 2 == 0)
        if not function[index][6]:
            set_function(index, 6, index % 2 == 0)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            set_function(30 + dy, 30 + dx, max(abs(dx), abs(dy)) != 1)
    for index in range(9):
        if index != 6:
            set_function(8, index, False)
            set_function(index, 8, False)
    for index in range(8):
        set_function(size - 1 - index, 8, False)
        set_function(8, size - 1 - index, False)
    set_function(size - 8, 8, True)

    best: list[list[bool]] | None = None
    best_score: int | None = None
    for mask in range(8):
        modules = [row[:] for row in base]
        bit_index = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right -= 1
            upward = ((right + 1) & 2) == 0
            for vertical in range(size):
                row = size - 1 - vertical if upward else vertical
                for column in (right, right - 1):
                    if not function[row][column]:
                        value = bool(code_bits[bit_index]) if bit_index < len(code_bits) else False
                        modules[row][column] = value ^ _qr_mask(mask, row, column)
                        bit_index += 1
            right -= 2
        _qr_draw_format(modules, mask)
        score = _qr_penalty(modules)
        if best_score is None or score < best_score:
            best, best_score = modules, score
    assert best is not None
    return ["".join("1" if value else "0" for value in row) for row in best]
