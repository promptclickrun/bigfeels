# Site deployment

The landing page is a standalone static site in `site/`. It does not load the Python service, use analytics, or send memory data. Fonts are self-hosted. Demo content is synthetic and held only in page JavaScript.

## Local preview

```sh
python3 -m http.server 8734 --bind 127.0.0.1 --directory site
```

## Verification

```sh
python3 scripts/check-site.py
node --check site/app.js
```

Check the live page at widths 320, 390, 768, and 1440. Exercise all three example steps, installation navigation, clipboard copying and the manual-selection fallback, keyboard focus, and the brand-kit download. Run an accessibility scan and inspect mobile and desktop screenshots. `check-site.py` validates local assets, fragment links, heading/ID semantics, the em-dash ban, and external destination availability. Human copy review remains required.

## Publish

Commit the reviewed source branch before publishing. GitHub Pages serves the root of `gh-pages`; only the contents of `site/` belong there.

```sh
DEPLOY_COMMIT=$(git subtree split --prefix site HEAD)
git push origin "$DEPLOY_COMMIT:refs/heads/gh-pages"
git tag deploy-YYYYMMDD-HHMM "$DEPLOY_COMMIT"
git push origin deploy-YYYYMMDD-HHMM
```

Pages source must be `gh-pages` at `/`. The public address is https://promptclickrun.github.io/bigfeels/. Verify the Pages build names the exact deployment commit, then compare the live HTML, CSS, JavaScript, icon, logo, and ZIP with local bytes. Source and deploy branches are separate; neither changes the engine.

## Rollback

Make a new commit on the deployment branch restoring the prior deployment tag's site tree, then push normally. Do not force-push shared history. Verify the new build and live content before claiming rollback.

## Brand assets

`site/assets/BRAND.md` records palette, formats, and licensing. Logo text is outlined in the SVG and needs no installed font. The downloadable ZIP includes the icon, wordmark, light wordmark, raster versions, favicon, and usage notes.
