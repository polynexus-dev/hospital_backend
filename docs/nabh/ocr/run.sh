#!/bin/bash
cd "$(dirname "$0")"
pdftoppm -r 200 -gray -png ../NABH-HIS-EMR-Standards-V12.1.pdf p
ls p-*.png | xargs -P 6 -I{} sh -c 'tesseract "{}" "$(basename {} .png)" -l eng --psm 6 >/dev/null 2>&1'
for f in $(ls p-*.txt | sort -V); do echo "=== PAGE $f"; cat "$f"; done > ../std_ocr.txt
rm -f p-*.png
echo OCR_DONE
