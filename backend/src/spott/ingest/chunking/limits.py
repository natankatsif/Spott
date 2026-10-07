"""How long a chunk may be, in characters."""

MAX_MERGE_CHARS = 1500  # blocks are grouped, and neighbours merged, up to this
MAX_BLOCK_CHARS = 2500  # a longer text is split into parts of about MAX_MERGE_CHARS
OVERLAP_CHARS = 200  # each part repeats this much of the one before
