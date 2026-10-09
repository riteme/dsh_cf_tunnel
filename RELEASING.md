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

   Note that **`uv publish` does not read `~/.pypirc`**; it takes credentials from `--token`,
   `UV_PUBLISH_TOKEN`, or its own `uv auth login` store. The file is still worth having for
   `twine` and for the conventional place a token lives.
3. **TestPyPI account and token.** TestPyPI is a separate service with its own accounts and its own
   tokens: a pypi.org token is rejected there. Register at <https://test.pypi.org/account/register/>
   and create a token at <https://test.pypi.org/manage/account/token/>, then add it as a second
   entry in `~/.pypirc` and/or store it with uv:

   ```ini
   [distutils]
   index-servers =
       pypi
       testpypi

   [pypi]
   username = __token__
   password = pypi-AgEIcHlwaS5vcmc...

   [testpypi]
   repository = https://test.pypi.org/legacy/
   username = __token__
   password = pypi-AgEIcHlwaS5vcmc...
   ```

   ```console
   uv auth login https://test.pypi.org/legacy/ --token pypi-...
   uv auth login https://upload.pypi.org/legacy/ --token pypi-...
   ```

   `uv auth dir` prints where the stored credentials live.
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

Upload with uv (it authenticates from `uv auth login` or from `--token`/`UV_PUBLISH_TOKEN`; it
never reads `~/.pypirc`):

```console
uv publish --dry-run --publish-url https://test.pypi.org/legacy/ dist/*   # shape check, no upload
uv publish --publish-url https://test.pypi.org/legacy/ dist/*             # real upload
```

`twine` is the alternative, and it does read `~/.pypirc`:

```console
python -m twine upload --repository testpypi dist/*
```

Then install from TestPyPI exactly as a stranger would. TestPyPI does not mirror PyPI, so the
dependencies (`segno`) have to come from the real index. Both forms work; the pip-style flags are
the ones a user is most likely to copy:

```console
uv venv --python 3.12 /tmp/verify --seed
/tmp/verify/bin/pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    dsh-cf-tunnel==0.1.0
/tmp/verify/bin/dsh-cf-tunnel --help
/tmp/verify/bin/dsh-cf-tunnel --cloudflared-bin /missing --dsh-bin /missing   # exit 1 + guidance

# uv's own spelling, if you prefer it:
uv pip install --index https://test.pypi.org/simple/ --index https://pypi.org/simple/ \
    --index-strategy unsafe-best-match dsh-cf-tunnel==0.1.0
```

Also install the sdist once (`pip install --no-binary :all: ...`), because that path exercises the
build backend and `MANIFEST.in` rather than just unzipping the wheel.

**Wait a moment before the first install.** TestPyPI's simple index and its PEP 658 metadata
endpoint do not update at exactly the same instant as the upload; an install issued seconds after
`uv publish` can report `dsh-cf-tunnel was not found in the package registry` even though the files
are listed. Retry before assuming the upload was wrong.

Open <https://test.pypi.org/project/dsh-cf-tunnel/> and read it as a user would: that page is what
the PyPI page will look like. The service puts a JavaScript challenge in front of plain HTTP
fetches, so check it in a browser; from a terminal, `twine check dist/*` runs the same description
validation PyPI does (it should print `PASSED` for both files).

TestPyPI enforces the same "cannot delete, cannot reuse" rule as PyPI, but only for TestPyPI: a
version burned there can still be published normally on PyPI. If the rehearsal needs a second
attempt, either bump the version or rehearse with a throwaway one such as `0.1.0.dev1`.

## 6. Publish

```console
uv publish dist/*          # uv auth login / UV_PUBLISH_TOKEN; uv never reads ~/.pypirc
# or: python -m twine upload dist/*        # twine does read ~/.pypirc
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
