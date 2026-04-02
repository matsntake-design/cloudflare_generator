実記事生成フェーズ1
====================

1. PowerShell でこのフォルダを開く
2. 次を実行
   python build_master_from_feeds.py

   もし対象サイトを変えたいときは siteId を後ろに並べます
   例:
   python build_master_from_feeds.py goldennews hatima oreteki itainews hamusoku

3. master_articles.json ができたら、次を実行
   python generate_pages.py

4. output フォルダの中身を Cloudflare Pages に再デプロイ

注意
----
- popular_sites.json の baseUrl をそのまま使います
- http を勝手に https に変えません
- ただし、サイト自身が feed 内で https の記事URLや画像URLを返してきた場合は、そのまま採用します
