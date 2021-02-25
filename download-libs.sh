#!/bin/sh

set -ex

export ASSETS=snektalk/assets
rm -rf $ASSETS/lib
mkdir -p $ASSETS/lib

rm -rf node_modules
mkdir node_modules
npm install monaco-editor
cp -r node_modules/monaco-editor/min/vs/ $ASSETS/lib/vs/
rm -rf node_modules
rm package-lock.json

wget https://requirejs.org/docs/release/2.3.6/minified/require.js
mv require.js $ASSETS/lib/require.min.js

wget https://cdnjs.cloudflare.com/ajax/libs/split.js/1.2.0/split.min.js
mv split.min.js $ASSETS/lib/split.min.js

wget https://cdnjs.cloudflare.com/ajax/libs/fuse.js/3.4.6/fuse.min.js
mv fuse.min.js $ASSETS/lib/fuse.min.js
