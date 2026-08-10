# Putting the map on pophealthmap.uk

Not done yet, because `pophealthmap.uk` is not registered. Until it is, the
site lives at https://harv334.github.io/pophealthmap/.

This is written down rather than left as a `CNAME` file, because a `CNAME`
committed before the domain resolves takes the site **offline**: GitHub Pages
starts 301ing the `github.io` address to a hostname that does not exist. That
is not a theory, it is what happened here, and it is why the file was removed
again.

## Order of operations

Register the domain **first**, then point it, then add the `CNAME`. Doing it in
any other order breaks the working site for the length of the gap.

1. **Register** `pophealthmap.uk` with any UK registrar.

2. **Point it at GitHub Pages.** For the apex, four `A` records:

   ```
   185.199.108.153
   185.199.109.153
   185.199.110.153
   185.199.111.153
   ```

   And a `CNAME` record on `www` to `harv334.github.io`.

   If the DNS is on Cloudflare, set these to **DNS only**, not proxied. A
   proxied record stops Pages from issuing its certificate, and the site serves
   an HTTPS warning instead.

3. **Wait for it to resolve.** Check before going further:

   ```bash
   nslookup pophealthmap.uk        # must return the four addresses
   ```

4. **Then** add the `CNAME` file and push:

   ```bash
   echo pophealthmap.uk > CNAME
   git add CNAME && git commit -m "Point the site at pophealthmap.uk" && git push
   ```

5. In the repo's **Settings → Pages**, confirm the custom domain is set and
   tick **Enforce HTTPS** once the certificate has been issued. That can take
   up to an hour after DNS propagates.

## Afterwards

Two things reference the domain and should be checked once it is live:

- `worker/wrangler.toml` lists the allowed origins for the AI panel. It already
  names `https://pophealthmap.uk` and `https://www.pophealthmap.uk`, so the
  panel will work on the new domain without a change. It also still lists the
  `github.io` origin, which you can drop once nothing uses it.
- `README.md` gives the live URL as the domain.
