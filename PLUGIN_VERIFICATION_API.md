# Plugin Verification API - KytyPS5 Studio

Some file hosts only reveal the real download link after an in-browser check (for example a
Cloudflare Turnstile box). Plugin backends have no browser, so Studio lends them one.

## `studio.resolve_link(url, selector, title="Verification", timeout=300, reveal_after=4)`

Opens `url` in a Studio window and returns the `href` of the element matching `selector`
as soon as the page fills it in.

- The window starts hidden and is shown after `reveal_after` seconds, so the user can finish
  any check the page asks for. Studio never clicks or solves the check itself.
- Returns the first `https://` href that differs from the page URL.
- Blocks the calling thread, so call it from a worker thread, not from a route handler.
- Raises `RuntimeError` when the user closes the window, after `timeout` seconds, or when
  Studio is not running as the desktop app. Raises `ValueError` for non-https URLs.

Always validate the returned URL (scheme, expected host) before downloading from it.

## Example: VikingFile

VikingFile pages expose the file as `<a id="download-link">` once the check passes:

```python
def download_worker(job, page_url):
    set_job(job, state="waiting_verification", message="Complete the check in the VikingFile window")
    link = studio.resolve_link(page_url, "#download-link", title="VikingFile")
    if not valid_resolved_download_url(link):  # https + vikingfile host only
        raise ValueError("VikingFile returned an unexpected download link")
    save_stream(link, referer=page_url)
```
