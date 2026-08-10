# The domain

The map is served at **https://pophealth.uk**, on GitHub Pages, with DNS at
Cloudflare.

## How it is wired

| Record | Name | Value |
|--------|------|-------|
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| CNAME | `www` | `harv334.github.io` |

All five are **DNS only** in Cloudflare, not proxied. Proxying them stops
GitHub validating the domain and issuing its certificate, and visitors get an
HTTPS warning instead of the map. You can tell which mode is live by asking for
the A records: unproxied returns the four GitHub addresses above, proxied
returns Cloudflare's.

The `CNAME` file in the repo root holds `pophealth.uk`. It is what tells GitHub
which repo to serve for this domain. Without it, requests reach GitHub and come
back as a 404, because the DNS is pointing at GitHub correctly but GitHub has
nothing tying the hostname to this repository.

## If you ever move the domain

Do it in this order. The order is the whole point of this file.

1. Register the new name and confirm it exists at the registry, not just in a
   Cloudflare dashboard. For `.uk`, `https://rdap.nominet.uk/uk/domain/<name>`
   returns a 404 for a name nobody owns. Cloudflare will happily let you add a
   zone and fill it with DNS records for a domain you have not bought, and it
   looks identical to one you have.
2. Add the records above.
3. Check it resolves before touching the repo: `nslookup <name>` must return
   the four GitHub addresses.
4. Only then update `CNAME` and push.

Doing step 4 first takes the site **offline**. GitHub Pages starts redirecting
the working `github.io` address to a hostname that does not resolve, so both
addresses break. That is not hypothetical; it is what happened here, and it is
why the file was removed again and the domain finished afterwards.

## Also referencing the domain

- `worker/wrangler.toml` allows `https://pophealth.uk` and
  `https://www.pophealth.uk` as origins for the AI panel. A domain change needs
  this changed and the Worker redeployed, or the panel silently stops
  answering.
- `README.md` gives the live URL.
