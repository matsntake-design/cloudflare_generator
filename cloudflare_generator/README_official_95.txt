95サイト正式運用セット
    ======================

    これは、現時点で安定確認が取れている 95 サイトを正式採用するための整理版です。

    使うファイル
    ------------
    1. build_official_builtins_master_from_sources.py
       - 正式ルート起動用
       - 内部で build_safe_builtins_master_from_sources.py を呼びます

    2. refresh_official_95.ps1
       - 正式ルートの一括実行用
       - 以下を順に実行します
         - build_official_builtins_master_from_sources.py
         - validate_master_articles.py
         - generate_pages.py

    3. safe_builtin_site_ids.json
       - 正式採用 95 サイトのリストです
       - サイトを増減するときは、まずこのファイルを触ります

    4. build_master_from_sources.py
       - 共通取得処理の本体です
       - 直接いじると影響が大きいので、普段はそのままにしてください

    普段の実行手順
    --------------
    PowerShell でこのフォルダを開いてから、

        .\refresh_official_95.ps1

    または個別に、

        python .\build_official_builtins_master_from_sources.py
        python .\validate_master_articles.py
        python .\generate_pages.py

    旧ルートの扱い
    --------------
    - build_priority_builtins_master_from_sources.py
      48サイト時代のルート。バックアップ用として残してもよいですが、正式運用では使いません。

    - audit_builtin_site_coverage.py
      全106サイトの棚卸し用。正式運用ではなく、次の拡張候補を調べるときだけ使います。

    今は保留にしているサイト
    ------------------------
    今回は次の11サイトを正式採用から外しています。
    理由は、古すぎる日時混入、タイトル要確認、または監査上の安全性確保です。

    - 働くモノニュース : 人生VIP職人ブログwww
- ブラブラブラウジング
- 映画.net
- 2MONKEYS.JP
- ウマ娘まとめちゃんねる
- ポケモンまとめ速報！！
- ぽけりん
- ポケくんまとめ
- PCパーツまとめ
- 海外の万国反応記
- 海外のお前ら　海外の反応

    補足
    ----
    - popular_sites.json の http は勝手に https に変えない運用のままです
    - master_articles.json は同梱していますが、基本は refresh_official_95.ps1 で再生成してください
    - generate_pages.py は /v1/sites と /v1/site-api の両方を生成します
