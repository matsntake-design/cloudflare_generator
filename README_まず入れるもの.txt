まず GitHub に入れるもの
=========================

このセットは、今確認できた「安定実績がある generator 一式」だけで、
GitHub Actions から Cloudflare Pages へ手動デプロイするための最小構成です。

入っているもの
--------------
- popular_sites.json
- cloudflare_generator/build_master_from_sources.py
- cloudflare_generator/generate_pages.py
- cloudflare_generator/validate_master_articles.py
- cloudflare_generator/README.txt
- .github/workflows/current-stable-manual.yml
- .gitignore

まだ入れていないもの
--------------------
- official_95 系の整理済み一式
- latest / sites / popular / full の分割 workflow
- build_latest_master_from_sources.py などの分割 build script

理由
----
今こちらで確認できた zip には、
分割 workflow が呼んでいる専用スクリプトがまだ含まれていません。
そのため、最初は current stable manual deploy 1本で動作確認するのが安全です。

次の順番
--------
1. このセットを GitHub repo に入れる
2. Cloudflare API Token を作る
3. GitHub Secrets を入れる
4. Actions で current-stable-manual.yml を Run workflow する
5. Cloudflare Pages に output が反映されたか確認する

注意
----
popular_sites.json の http は、そのまま維持してください。
勝手に https に直さないでください。
