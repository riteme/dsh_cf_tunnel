# Releasing dsh-cf-tunnel

The package is published to the public PyPI as `dsh-cf-tunnel`. Every version is public and
permanent: a file cannot be deleted, only yanked, and a version number can never be reused.

## 1. One-time prerequisites

1. **PyPI account** at <https://pypi.org/account/register/>. Enable two-factor authentication;
   PyPI requires it for uploads.
2. **API token.** Create one at <https://pypi.org/manage/account/token/> scoped to the project
   (or to "Entire account" for the very first upload, because the project does not exist yet).
   Store it in `~/.pypirc`:

   ```ini
   [distutils]
   index-servers =
       pypi

   [pypi]
   username = __token__
   password = pypi-AgEIcHlwaS5vcmc...
   ```

   `chmod 600 ~/.pypirc`. Never commit this file.
3. **TestPyPI token** (optional but recommended) from <https://test.pypi.org/manage/account/token/>,
   as a second `[testpypi]` entry pointing at `https://test.pypi.org/legacy/`.
4. **Identity metadata.** Fill in `authors` and `urls` in `pyproject.toml`. PyPI shows both on the
   project page, and they cannot be edited out of an already-published version's metadata.
5. **License and authorship clearance.** The package links against nothing copyleft, ships only its
   own MIT-licensed code, and does not bundle `cloudflared` or `dsh`. Confirm with whoever owns
   release approval that publishing it publicly under MIT is intended, since the monorepo's own
   vendor components carry a proprietary Houmo EULA.

## 2. Before every release

```console
cd dsh_cf_tunnel
uv venv --python 3.12 .venv            # any supported interpreter
uv pip install -e '.[dev]'

pytest                                  # unit and integration tests
DSH_CF_TUNNEL_E2E=1 pytest -m e2e       # boots a real quick tunnel; needs dsh + cloudflared
ruff check src tests
black --check --line-length 120 src tests
isort --check --profile black --line-length 120 src tests
```

The e2e suite is the one check that exercises the real Cloudflare edge, the real `dsh`, the auth
shim, and the generated patch together; do not skip it for a release.

## 3. Version bump

Three places must agree:

- `pyproject.toml` → `[project] version`
- `src/dsh_cf_tunnel/__init__.py` → `__version__`
- `CHANGELOG.md` → a new `## [x.y.z] - YYYY-MM-DD` section, moving anything from Unreleased

Then commit and tag:

```console
git add -A && git commit -m "Release dsh-cf-tunnel 0.1.0"
git tag dsh-cf-tunnel-v0.1.0
git push origin HEAD && git push origin dsh-cf-tunnel-v0.1.0
```

## 4. Build and inspect

```console
rm -rf build dist
uv build
```

Verify the artifacts before uploading:

```console
python -m zipfile -l dist/dsh_cf_tunnel-0.1.0-py3-none-any.whl     # wheel contents
tar tzf dist/dsh_cf_tunnel-0.1.0.tar.gz | head -30                  # sdist contents
```

The wheel must contain `dsh_cf_tunnel/`, `py.typed`, and
`dsh_cf_tunnel-0.1.0.dist-info/{METADATA,entry_points.txt,licenses/LICENSE}` — and nothing else.
The README users will read on PyPI is the rendered `METADATA`, so review it with
`python -m readme_renderer` or by installing the wheel and opening the project page on TestPyPI.

## 5. Rehearse on TestPyPI

```console
uv publish --index testpypi dist/*
# or: python -m twine upload --repository testpypi dist/*

uv venv --python 3.12 /tmp/verify && /tmp/verify/bin/pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    dsh-cf-tunnel
/tmp/verify/bin/dsh-cf-tunnel --help
```

TestPyPI has the same "cannot delete, cannot reuse" rule, so this is a real rehearsal.

## 6. Publish

```console
uv publish dist/*          # reads ~/.pypirc, or use UV_PUBLISH_TOKEN
```

Confirm the result:

- <https://pypi.org/project/dsh-cf-tunnel/> renders the README and the MIT license
- `pip install dsh-cf-tunnel` works from a clean environment
- the tag shows up in the repository's releases

## 7. If something is wrong

- A broken upload cannot be replaced, only superseded: bump the patch version and publish again.
- A release that must not be installed can be yanked from the PyPI project page; yanking hides it
  from resolvers but leaves the files downloadable by exact version.
- Metadata cannot be edited in place for a published version either, so mistakes there also mean a
  new version.
