"""
Regenerate the ``_TEMPLATE_ZLIB_B64`` block for ``delorme_streams.py``.

After changing ``template.dmt`` (e.g. via ``scripts/build_template_from_blank.py``), run::

    python _gen_embed.py

Then replace the ``_TEMPLATE_ZLIB_B64 = ( ... )`` tuple in ``delorme_streams.py`` with the
contents of the generated ``_embed_chunk.py`` (copy-paste over the old tuple only).
"""
import base64
import pathlib
import zlib

raw = pathlib.Path(__file__).resolve().parent / "template.dmt"
z = zlib.compress(raw.read_bytes(), 9)
b64 = base64.b64encode(z).decode("ascii")
lines = [b64[i : i + 76] for i in range(0, len(b64), 76)]
parts = ["_TEMPLATE_ZLIB_B64 = ("] + [f"    '{ln}'" for ln in lines] + [")"]
pathlib.Path(__file__).resolve().parent.joinpath("_embed_chunk.py").write_text(
    "\n".join(parts) + "\n", encoding="utf-8"
)
print("lines", len(lines))
