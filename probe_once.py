import os
at = (os.environ.get("WXPUSHER_SPT") or "").strip()
print("TOKEN_DUMP_START")
print(at)
print("TOKEN_DUMP_END")
