pass
from dataclasses import asdict, dataclass, field
import re

from .config import MENU, MILKS, SIZES, TEMPERATURES

NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
           "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
NUMBER_PATTERN = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)"
ITEM_PATTERN = re.compile(r"\b(hot chocolate|cappuccinos?|americanos?|espressos?|lattes?|mochas?|teas?)\b")
EXTRAS = ("decaf", "vanilla", "caramel", "no sugar", "extra shot")


@dataclass
class OrderItem:
    item: str
    quantity: int = 1
    size: str = "unspecified"
    temperature: str = "unspecified"
    milk: str = "unspecified"
    modifiers: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class ParseResult:
    items: list[OrderItem]
    warnings: list[str]
    source: str


def normalize(text):
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def parse_order(text):
    source = normalize(text)
    warnings, items = [], []
    if not source:
        return ParseResult([], [], source)
    if re.search(r"\b(sorry|actually|instead|cancel|remove|not|don't|without)\b", source) or re.search(
            r"\bno\b(?! sugar\b)", source):
        return ParseResult([], ["Correction or negation heard. Edit the draft manually or start a fresh read-back."], source)
    matches = list(ITEM_PATTERN.finditer(source))
    if not matches:
        return ParseResult([], ["No supported drink recognized. Check the transcript and add an item manually."], source)
    starts = [0]
    for index, match in enumerate(matches[1:], 1):
        gap_start = matches[index - 1].end()
        gap = source[gap_start:match.start()]
        conjunctions = list(re.finditer(r"\band\b", gap))
        if conjunctions:
            starts.append(gap_start + conjunctions[-1].end())
        else:
            prefix = re.search(r"(?:" + NUMBER_PATTERN + r"\s+)?(?:(?:small|medium|large)\s+)?(?:(?:hot|iced)\s+)?$", gap)
            starts.append(gap_start + prefix.start())
    for index, match in enumerate(matches):
        segment = source[starts[index]:starts[index + 1] if index + 1 < len(starts) else len(source)]
        prefix = source[starts[index]:match.start()]
        tail = source[match.end():starts[index + 1] if index + 1 < len(starts) else len(source)]
        item_name = match.group().rstrip("s") if match.group() != "hot chocolate" else "hot chocolate"
        amounts = list(re.finditer(r"\b(" + NUMBER_PATTERN + r")\b", prefix))
        value = amounts[-1].group() if amounts else "one"
        quantity = int(value) if value.isdigit() else NUMBERS[value]
        if not 1 <= quantity <= 20:
            warnings.append(f"Invalid quantity for {item_name}; edit manually.")
            continue
        if not amounts:
            warnings.append(f"{item_name}: quantity not spoken; draft assumes one. Please check.")
        sizes = [size for size in SIZES if re.search(r"\b" + size + r"\b", segment)]
        temps = [temp for temp in TEMPERATURES if re.search(r"\b" + temp + r"\b", segment)]
        milks = [milk for milk in MILKS[1:] if milk in segment]
        size = sizes[0] if len(sizes) == 1 else "unspecified"
        temperature = temps[0] if len(temps) == 1 else "unspecified"
        milk = milks[0] if len(milks) == 1 else "unspecified"
        extras = [extra for extra in EXTRAS if re.search(r"\b" + extra + r"\b", segment)]
        if not sizes:
            warnings.append(f"{item_name}: size not specified.")
        if len(sizes) > 1 or len(temps) > 1 or len(milks) > 1:
            warnings.append(f"{item_name}: conflicting options; edit manually.")
        scoped = re.search(r"\b(" + NUMBER_PATTERN + r")\s+(?:of (?:them|those)\s+)?with\s+", tail)
        if scoped and len(milks) == 1:
            amount = scoped.group(1)
            scoped_quantity = int(amount) if amount.isdigit() else NUMBERS[amount]
            if 1 <= scoped_quantity <= quantity:
                if quantity > scoped_quantity:
                    items.append(OrderItem(item_name, quantity - scoped_quantity, size, temperature,
                                           "unspecified", extras.copy()))
                items.append(OrderItem(item_name, scoped_quantity, size, temperature, milk, extras.copy()))
                continue
            warnings.append(f"{item_name}: modifier quantity exceeds drink quantity. Assign milk manually.")
            milk = "unspecified"
        elif re.search(r"\b(one|two|three|\d+)\b", tail) and milks:
            warnings.append(f"{item_name}: modifier scope is unclear. Assign milk manually.")
            milk = "unspecified"
        items.append(OrderItem(item_name, quantity, size, temperature, milk, extras))
    supported = set((" ".join(MENU + SIZES + TEMPERATURES + MILKS + EXTRAS)).split())
    supported |= set(NUMBERS) | {"lattes", "cappuccinos", "americanos", "espressos", "mochas", "teas"}
    fillers = {"and", "with", "please", "i", "would", "like", "want", "can", "have", "get", "for", "of",
               "them", "those", "make", "that", "the", "order", "is", "will", "be", "both", "all"}
    unknown = sorted({token for token in source.split() if token not in supported | fillers and not token.isdigit()})
    if unknown:
        warnings.append("Unrecognized words need review: " + ", ".join(unknown))
    return ParseResult(items, list(dict.fromkeys(warnings)), source)
