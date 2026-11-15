# UNAL DLHM source profile

This record pins the K07 and K18 references used to design the future U02-T2
comparison backend. It does not import or redistribute upstream example
images.

## Pinned sources

- Code repository: `unal-optodigital/DLHM`
- Code commit: `5d5273e2ebb08735c531815d3e4b100a4e3f3772`
- Documentation branch commit: `536e70f1e179bf285492343b989a478d093c32e3`
- Published project page: <https://unal-optodigital.github.io/DLHM/>
- Article: <https://doi.org/10.1364/AO.395672>

The site identifies itself as version 1.2 while the pinned Java source records
version 1.3. Implementations must therefore cite the commits above instead of
an ambiguous latest-version label.

## License boundary

Individual Java source files carry Apache License 2.0 headers and Universidad
Nacional de Colombia copyright notices. The repository does not contain a
root LICENSE or COPYING file, and the example PGM, JPG, BMP, and parameter
screenshots do not carry an explicit per-file license.

Consequently:

- algorithm and parameter research may cite the pinned Java source while
  preserving its headers and attribution;
- example images must not be committed or redistributed in this repository
  without separate permission;
- a local comparison fixture must record its source URL, pinned commit or
  blob identity, SHA-256, access date, and usage permission;
- CI must skip licensed-external fixtures rather than silently replacing them
  with unverified copies.

## Candidate local comparison pair

The clearest published example is the single-microorganism block on the pinned
site:

- hologram `c0025.jpg`, Git blob
  `f4218e67ca133e28ab2c2e66e46d9399ad153031`;
- reference `c0039.jpg`, Git blob
  `98510a0d67853ef87e68a8115eee67be754e69f6`;
- parameter screenshot `OneMicrorganism_ReconstructionParameters.jpg`, Git
  blob `dd537fde038a78e924b3f1dfb855021d977c89fc`;
- expected intensity image `OneMicrorganism_Intensity.jpg`, Git blob
  `8d5519fc90c45c583ec5774952525b1552928cef`.

The parameter values are embedded in an image. They must be transcribed and
independently checked before an executable benchmark configuration is added.
The published intensity JPG is a visual comparison source, not a bitwise
golden output.

## Separate propagation backend

The current U02-T1 core implements a plane-wave angular-spectrum scan. UNAL
DLHM uses point-source Kirchhoff-Helmholtz reconstruction with spherical
geometry. Its parameter contract includes:

- wavelength;
- reconstruction distance;
- source-to-screen distance;
- detector physical width and height;
- output-plane physical width and height;
- input and output sample pitches.

The UNAL profile must therefore be implemented as a separate
`point_source_kirchhoff_helmholtz` backend. It must not reuse the current
plane-wave result as a PASS fixture.

## Acceptance boundary

Until the example license, exact parameters, shared crop, intensity mapping,
and comparison threshold are fixed in advance:

- U02-T2 remains `BEKLİYOR`;
- comparison output remains `NOT_EVALUATED`;
- no physical-focus, reconstruction-accuracy, or customer-acceptance claim is
  permitted.
