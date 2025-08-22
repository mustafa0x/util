#!/bin/bash
set -euxo pipefail

# Precursor
# 1. Enable Associated Domains
#   1. Visit https://developer.apple.com/account/resources/identifiers/list
#   2. Open the App ID
#   3. Enable Associated Domains
# 2. Add in xcode (see https://capacitorjs.com/docs/guides/deep-links#add-associated-domain)

# Docs
# - https://developer.android.com/training/app-links/verify-android-applinks
# - https://capacitorjs.com/docs/guides/deep-links

# Test
# - https://developers.google.com/digital-asset-links/tools/generator
# - curl -v https://app-site-association.cdn-apple.com/a/v1/read.tafsir.one
# - https://branch.io/resources/aasa-validator/

#################################################
# Android
#################################################
if ! [ -f assetlinks.json ]; then
    PLAY_CONSOLE_DEV_ID=$(jq -r ._config.play_console_dev_id package.json)
    PLAY_CONSOLE_APP_ID=$(jq -r ._config.play_console_app_id package.json)
    echo 'Create assetlinks.json (copy from play console) then run the script again'
    open https://play.google.com/console/u/0/developers/$PLAY_CONSOLE_DEV_ID/app/$PLAY_CONSOLE_APP_ID/keymanagement
    exit 1
fi

#################################################
# IOS
#################################################
DOMAIN=$(jq -r .config.domain package.json)
TEAM_ID=$(rg --no-line-number --max-count 1 'DEVELOPMENT_TEAM = (.*?);' -or '$1' ios/App/App.xcodeproj/project.pbxproj)
APP_ID=$(jq -r .config.app_id package.json)

cat <<EOF >> android/app/src/main/AndroidManifest.xml
<intent-filter android:autoVerify="true">
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    <data android:scheme="https" android:host="$DOMAIN" />
</intent-filter>
EOF
$EDITOR android/app/src/main/AndroidManifest.xml

cat <<EOF > apple-app-site-association
{"applinks": {"apps": [],"details": [{"appID": "$TEAM_ID.$APP_ID","paths": ["*"]}]}}
EOF

#################################################
# Upload
#################################################
tar -cf - apple-app-site-association assetlinks.json | ssh $DEPLOY_HOST "
    mkdir -p /srv/apps/$APP_NAME/.well-known
    cd /srv/apps/$APP_NAME/.well-known
    tar -xf -
"

echo 'Caddy: add `header /.well-known/apple-app-site-association Content-type application/json`'
