import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "doc-parser"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.form16 import parse_form16_text, form16_to_dict

text = """
Name of the Employee: Priya Nair
PAN of the Employee: ABCDE1234F
Assessment Year: 2026-27
"""

parsed = parse_form16_text(text)
result = form16_to_dict(parsed)
print(f"Extracted Name: '{result.get('employee_name')}'")
