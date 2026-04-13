_ANNOTATE_WORKSPACE = "DeLormeComponents/DeLorme.Annotate.Workspace"
_STREAM_ANNOTATE_FILENAMES = f"{_ANNOTATE_WORKSPACE}/Annotate.Filenames"
_STREAM_ANNOTATE_ACTIVE_FILENAMES = f"{_ANNOTATE_WORKSPACE}/Annotate.ActiveFilenames"
_DEFAULT_EMBED_TXT_STREAM = f"{_ANNOTATE_WORKSPACE}/Centerline.txt"


def embed_centerline_txt_stream(
    dmt_bytes: bytes,
    centerline_txt_bytes: bytes,
    *,
    stream_path: str = _DEFAULT_EMBED_TXT_STREAM,
) -> bytes:
    """
    Add/replace a ``Centerline.txt`` payload *inside* the .dmt OLE container.

    The USER asked to store the generated TXT "where the draw layer lives"; in DeLorme
    projects that is under ``DeLormeComponents/DeLorme.Annotate.Workspace``.

    This does **not** make XMap automatically import/activate the text; it's a convenient,
    portable place to stash the exact TXT you would otherwise import via Draw → Import.
    """
    import os
    import shutil
    import tempfile

    import olefile

    try:
        from extract_msg import OleWriter
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Embedding Centerline.txt into .dmt requires `extract-msg` (OleWriter). "
            "Install it and retry."
        ) from e

    # OleWriter works with files; materialize to a temp path, edit, then read back.
    fd_in, tmp_in = tempfile.mkstemp(suffix=".dmt", prefix="kmz_cl_in_")
    os.close(fd_in)
    fd_out, tmp_out = tempfile.mkstemp(suffix=".dmt", prefix="kmz_cl_out_")
    os.close(fd_out)
    try:
        with open(tmp_in, "wb") as f:
            f.write(dmt_bytes)

        with olefile.OleFileIO(tmp_in) as ole:
            w = OleWriter()
            w.fromOleFile(ole)

        parts = _ole_stream_path_to_parts(stream_path)
        # Replace if present, else add.
        try:
            w.editEntry(parts, data=centerline_txt_bytes)
        except Exception:
            w.addEntry(parts, data=centerline_txt_bytes)

        w.write(tmp_out)
        with open(tmp_out, "rb") as f:
            return f.read()
    finally:
        for p in (tmp_in, tmp_out):
            try:
                os.unlink(p)
            except OSError:
                pass
