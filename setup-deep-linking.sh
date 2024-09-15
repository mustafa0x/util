#!/bin/bash
set -euxo pipefail

# Precursor
# 1. Enable Associated Domains
#   1. Visit https://developer.apple.com/account/resources/identifiers/list
#   2. Open the App ID
#   3. Enable Associated Domains
# 2. https://capacitorjs.com/docs/guides/deep-links#add-associated-domain

# Docs
# - https://developer.android.com/training/app-links/verify-android-applinks
# - https://capacitorjs.com/docs/guides/deep-links

# Test
# - https://developers.google.com/digital-asset-links/tools/generator
# - curl -v https://app-site-association.cdn-apple.com/a/v1/read.tafsir.one
# - https://branch.io/resources/aasa-validator/

FINGERPRINT=$(keytool -list -v -keystore android/$KEY_STORE_PATH | grep SHA256: | awk '{print $2}')

DOMAIN=$(jq -r .config.domain package.json)
TEAM_ID=$(jq -r .config.team_id package.json)
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

cat <<EOF > assetlinks.json
[{
  "relation": ["delegate_permission/common.handle_all_urls"],
  "target" : { "namespace": "android_app", "package_name": "$APP_ID",
               "sha256_cert_fingerprints": ["$FINGERPRINT"] }
}]
EOF

tar -cf - apple-app-site-association assetlinks.json | ssh $DEPLOY_HOST "
    mkdir -p /srv/apps/$APP_NAME/.well-known
    cd /srv/apps/$APP_NAME/.well-known
    tar -xf -
"
