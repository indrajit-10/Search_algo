# assets

Only one thing lives here, and only to make the live-look page work offline.

`123g_static_R1.css` is the site's own `static_R1.css`, as supplied. The live
page at `/live` links the CDN copy first and falls back to this one, so it looks
right on a machine that cannot reach `c.123g.us` — which is how it was built.

**It is a partial picture.** That file opens with

```css
@import url("styleopt_R1.css");
@import url("modal_window_R1.css");
```

and neither of those is here, nor is `sub_categories_R1.css`. More to the point,
**the rules for `ul.sub-cat` — the result list itself — are in one of the files
that is missing**, so the layout overrides in `serve.py` are written defensively
with `!important` rather than tuned against the real cascade.

Drop the other three files in beside this one and the page will render exactly
as production does. Nothing else in the repo reads them.
