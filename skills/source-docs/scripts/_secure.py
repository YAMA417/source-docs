#!/usr/bin/env python3
"""読み込んではいけないファイルの判定。同梱スクリプトが共有する。

このスキルは他人のリポジトリを読む。認証情報が入りうるファイルは
**そもそも開かない。** 開いてから中身を判定するのでは遅い。

判定は名前と場所だけで行う。中身を見ないので確実に安全側へ倒れるが、
そのぶん取りこぼしはある。最後の砦にしない。
"""

import os

# ファイル名がこれと完全一致するものは開かない
SECRET_FILENAMES = frozenset({
    ".netrc", "_netrc", ".npmrc", ".pypirc", ".htpasswd", ".dockercfg",
    ".terraformrc", "terraform.tfvars", "terraform.tfvars.json",
    "credentials", "credentials.json", "credential.json",
    "service-account.json", "serviceaccount.json", "gcloud-key.json",
    "secrets.json", "secrets.yaml", "secrets.yml", "secret.yaml", "secret.yml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "master.key", "keystore.jks", "google-services.json",
    "GoogleService-Info.plist", "auth.json", "token.json",
    ".envrc", ".git-credentials", ".pgpass", ".s3cfg", ".boto",
    ".msmtprc", ".my.cnf", "id_rsa.pub", "known_hosts",
})

# 拡張子がこれなら開かない。鍵そのもの
SECRET_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk",
    ".asc", ".gpg", ".kdbx", ".crt", ".cer", ".der",
    ".p8",          # Apple の署名鍵
    ".tfvars",      # Terraform の変数ファイル。値が直に入る
    ".tfvars.json",
    ".pkcs12", ".jceks", ".mobileprovision",
)

# .env で始まるものは原則開かない。ただし下の例外は読む
ENV_PREFIX = ".env"

# 変数名を知るために必要なので読む。**値は読まない**（SKILL.md の規則）
ENV_SAFE = frozenset({
    ".env.example", ".env.sample", ".env.template", ".env.dist",
    ".env.example.local",
})

# このディレクトリの配下は丸ごと読まない
SECRET_DIRS = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker",
    "credentials", "secrets", "private", ".secrets",
})


def is_secret_file(path):
    """ファイル名だけで、開いてはいけないかを判定する。"""
    name = path.replace("\\", "/").rstrip("/").split("/")[-1]
    lower = name.lower()

    if lower in ENV_SAFE:
        return False
    if lower == ENV_PREFIX or lower.startswith(ENV_PREFIX + "."):
        return True
    if lower in {n.lower() for n in SECRET_FILENAMES}:
        return True
    if lower.endswith(SECRET_SUFFIXES):
        return True
    return False


def is_secret_path(path):
    """ファイル名に加えて、置かれているディレクトリでも判定する。

    区切り文字は `/` と `\\` の両方を見る。Linux 上で Windows 形式のパスを
    渡されても判定できるようにするため。
    """
    if is_secret_file(path):
        return True
    normalized = path.replace("\\", "/").replace(os.sep, "/")
    parts = normalized.split("/")[:-1]
    return any(p.lower() in SECRET_DIRS for p in parts)


def is_unsafe_to_open(path):
    """実際に開く直前の最終判定。名前とシンボリックリンクの両方を見る。

    シンボリックリンクは開かない。名前が `schema.ts` でも、リンク先が
    `~/.ssh/id_rsa` である可能性を名前だけでは排除できない。
    """
    if is_secret_path(path):
        return True
    try:
        return os.path.islink(path)
    except OSError:
        return True


def filter_paths(paths):
    """読んでよいパスだけを返す。"""
    return [p for p in paths if not is_secret_path(p)]
