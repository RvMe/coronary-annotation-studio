# Contributing

Start with a small issue describing the annotation workflow or interoperability
problem. Include the application version, OS/architecture and a minimal synthetic
reproduction. Never upload patient images, labels containing identifiers, private
database files, access credentials or an unredacted diagnostic log.

Install from source as described in the README. Run `python -m unittest discover
-s tests -v`. Add meaningful tests for changed coordinate transforms, annotation
round trips, recovery and project/reader isolation. UI changes must be checked in
both English and Simplified Chinese, including a draft present during switching.

Use stable language-independent field names and enums. Do not treat unlabeled
regions as negative, infer anatomical ownership from filenames, change geometry
silently, or turn reader completion into clinical acceptance. New input adapters
must demonstrate mapping into native LPS millimetres and quantify subvoxel error.

Contributions are accepted under Apache-2.0. Preserve copyright and attribution
for third-party material and describe any license implications in the pull request.
