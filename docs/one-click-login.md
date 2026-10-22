# Prijava v e.FE z enim klikom (beta1.9.0)

## Uporaba

Ko je aplikacija nameščena, odpri **FE/Spletna-ucilnica/POVEZI.html**.
Datoteka te sama preusmeri na zasebni prijavni prehod NAS, ta odpre Chromium.
Prijavi se prek uradnega UL SSO. Portal sam preveri dostop do Moodle predmetov,
shrani samo potrebne Moodle piškotke v obstoječo NAS skrivnost, zapre Chromium sejo
in sproži sinhronizacijo brez kopiranja piškotkov ali terminala.

## Enkratna namestitev v TrueNAS

1. Potrebuješ **Tailscale IPv4 naslov gostitelja TrueNAS**, navadno 100.x.y.z.
   Če Tailscale teče samo v ločenem vsebniku in naslov ni na samem hostu,
   povezovanje Docker portov na ta IP ne bo delovalo. Ne zamenjaj ga z javnim IP.
2. Odpri **Apps → Installed Applications → fe-moodle-sync → Edit**.
3. V Custom App YAML uporabi posodobljeni
   [deploy/truenas-compose.yaml](../deploy/truenas-compose.yaml).
   OHRANI vse obstoječe mount poti in podatkovne zbirke.
4. V dveh pojavih nadomesti Compose spremenljivko FE_LOGIN_PRIVATE_IP
   z dejanskim zasebnim Tailscale IPv4 NAS-a (ne z 0.0.0.0).
   Spremenljivka se uporablja v public URL in port-binding v storitvi fe-login.
5. Shrani Compose, ki poleg obstoječega sync zažene še
   fe-login-browser in fe-login. Na telefonu vključi isto Tailscale mrežo.
6. Odpri **FE/Spletna-ucilnica/POVEZI.html** iz Syncthinga oziroma
   lokalnega pregleda e.FE. Vmesni koraki na terminalu niso potrebni.

## Kako je zaščiteno

- Zunaj Compose je dosegljiv samo portal na Tailscale IPv4:17899.
- Chromium, noVNC in Selenium WebDriver nimajo neposredno objavljenih portov.
- Portal zahteva naključno zmožnost v povezavi FE datoteke;
  ta je prenesena kot URL fragment, ne kot HTTP zahteva.
- Portal seji izda kratkoživ HttpOnly in SameSite=Strict piškotek ter
  preverja dostop do HTTP in WebSocket noVNC.
- UL geslo in piškotki niso shranjeni v FE mapi. Žeton za dostop do
  zasebnega portala v POVEZI.html je občutljiv: te povezave ne deli.
- Docker socket ni mountan; login service ne more upravljati drugih vsebnikov.
- Podatek o prijavi se potrdi z bralnim Moodle API klicem, preden se zamenja
  vsebina obstoječe datoteke piškotkov (inode se ne spremeni).
- Zaradi HTTP dostopa uporabljaj izključno **šifrirano Tailscale omrežje**;
  ne objavi portala prek javne domene, Cloudflare Tunnel ali nezaščitenega LAN.

## Omejitve

To je enouporabniški prototip. Dejanski Chromium/UL SSO/noVNC potek
je treba po namestitvi še preveriti na TrueNAS-u in telefonu.
Za komercialno storitev je nujna ločitev uporabnikov in prednostna
uporaba odobrenega Moodle API ali institucionalne integracije, če je
e.FE podpira, skupaj s preveritvijo pravic do gradiv in varstva podatkov.
