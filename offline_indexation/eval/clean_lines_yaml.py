"""Clean up eval/lines.yaml per task 06 spec and save cleaned version."""

from pathlib import Path

import yaml

eval_path = Path("eval/lines.yaml")
with open(eval_path) as f:
    data = yaml.safe_load(f)

# IDs to remove entirely
remove_ids = {
    "line-14",  # OCR garbage: "Billbar fgre Corsultant IilRigiahikioh"
    "line-15",  # gold_line is just underscores (___...) — decoration
    "line-29",  # gold_line contains raw HTML tags </p></li>
    "line-32",  # Too general: "How many people participated?" — multiple events
    "line-39",  # OCR garbage: "st: 27 Matie 1918, rr. 14, municipiul Chișinig"
    "line-40",  # OCR garbage: "Atențiel Documentul conține date..." 
    "line-60",  # Too general: "What measures for access of participants?"
    "line-72",  # OCR garbage: "ISIVOL Veaceslar | vertie | speceeis-"
}

# Ukrainian gold lines — mark lang: uk and exclude from metric
uk_ids = {"line-12", "line-47", "line-55"}

cleaned = []
removed_count = 0
uk_marked = 0
fixed_58 = False

for entry in data:
    eid = entry.get("id", "")
    
    # Remove bad entries
    if eid in remove_ids:
        removed_count += 1
        continue
    
    # Mark Ukrainian lines
    if eid in uk_ids:
        entry["lang"] = "uk"
        entry["exclude_from_metric"] = True
        uk_marked += 1
    
    # Fix line-58: Buburuza = Божья коровка, not Бабочка  
    if eid == "line-58":
        entry["query"] = (
            'Где находится центр "Божья коровка" для детей, '
            'где предлагаются занятия музыкой и рисованием?'
        )
        fixed_58 = True
    
    cleaned.append(entry)

with open(eval_path, "w") as f:
    yaml.dump(cleaned, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)

pos = [e for e in cleaned if not e.get("is_negative")]
neg = [e for e in cleaned if e.get("is_negative")]
excluded = [e for e in cleaned if e.get("exclude_from_metric")]
print(f"Removed:    {removed_count} entries ({list(remove_ids)})")
print(f"Marked UK:  {uk_marked} ({list(uk_ids)})")
print(f"Fixed:      line-58 translation ({'done' if fixed_58 else 'FAIL'})")
print(f"Result:     {len(cleaned)} total = {len(pos)} positive + {len(neg)} negative")
print(f"Excluded:   {len(excluded)} (Ukrainian, not counted in metric)")
print(f"In metric:  {len(pos) - len(excluded)} positive entries")
