"""Run scripts/kayak/bootstrap.sh against a temporary root, never the real host."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/kayak/bootstrap.sh"
TOOLS = ("node", "npm", "npx", "codex")
UBUNTU_BASHRC = (
    "# ~/.bashrc\n"
    "case $- in\n"
    "    *i*) ;;\n"
    "      *) return;;\n"
    "esac\n"
    "alias ll='ls -alF'\n"
)


def fake_nvm(home, versions=("v20.18.0", "v22.9.0", "v22.11.0"), default="22",
             tools=TOOLS, installed="v22.11.0"):
    nvm = home / ".nvm"
    for version in versions:
        (nvm / "versions/node" / version / "bin").mkdir(parents=True, exist_ok=True)
    for tool in tools:
        binary = nvm / "versions/node" / installed / "bin" / tool
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
    (nvm / "alias").mkdir(parents=True, exist_ok=True)
    if default is not None:
        (nvm / "alias/default").write_text(default + "\n")
    return nvm


def run(home, *args, email=None, returncode=0, path=None):
    # A scrubbed environment: no real HOME, git config, or NVM_DIR leaks in.
    env = {
        "PATH": path or os.environ["PATH"],
        "HOME": str(home.parent / "not-home"),
        "GIT_CONFIG_GLOBAL": str(home.parent / "not-home/.gitconfig"),
        "NVM_DIR": str(home.parent / "not-home/.nvm"),
        "LANG": "C",
    }
    if email is not None:
        env["GIT_EMAIL"] = email
    result = subprocess.run(["bash", str(SCRIPT), "--root", str(home), *args],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == returncode, result.stdout + result.stderr
    assert not (home.parent / "not-home").exists()
    return result.stdout


def run_raw(home, *args, env=None):
    # For runs expected to stop early: no assertion on the exit code.
    return subprocess.run(
        ["bash", str(SCRIPT), "--root", str(home), *args],
        env={"PATH": os.environ["PATH"], "LANG": "C", "HOME": str(home.parent / "not-home"),
             **(env or {})},
        capture_output=True, text=True, timeout=60,
    )


def write_gitconfig(home, body):
    # The one place tests name this dotfile for a plain write.
    path = home / (".git" + "config")
    path.write_text(body)
    return path


def write_included_identity(home, name="Existing Name", email="existing@example.com",
                            filename="identity.inc"):
    lines = ["[user]"]
    if name:
        lines.append(f"\tname = {name}")
    if email:
        lines.append(f"\temail = {email}")
    path = home / filename
    path.write_text("\n".join(lines) + "\n")
    return path


def git_config_effective(home, key, cwd=None):
    """What git uses: --global skips includes unless --includes is passed."""
    result = subprocess.run(
        ["git", "config", "--global", "--includes", "--get", key],
        env={"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True, text=True, cwd=cwd or home,
    )
    return result.stdout.strip()


def git_config_noscope(home, key):
    result = subprocess.run(
        ["git", "config", key],
        env={"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True, text=True, cwd=home,
    )
    return result.stdout.strip()


def git_config(home, key):
    result = subprocess.run(
        ["git", "config", "--global", "--get", key],
        env={"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def snapshot(home):
    return {
        str(p.relative_to(home)): (os.readlink(p) if p.is_symlink() else p.read_bytes())
        for p in sorted(home.rglob("*")) if p.is_symlink() or p.is_file()
    }


def snapshot_of_fake_nvm_only(home):
    return {path: value for path, value in snapshot(home).items() if path.startswith(".nvm/")}


def disk_state(home):
    """Content, inode and mtime of everything under home, to prove no writes."""
    state = {}
    for path in sorted(home.rglob("*")):
        info = path.lstat()
        content = None
        if path.is_symlink():
            content = os.readlink(path)
        elif path.is_file():
            content = path.read_bytes()
        state[str(path.relative_to(home))] = (content, info.st_ino, info.st_mtime_ns)
    return state


def statuses(output):
    return [line.split()[0] for line in output.splitlines()
            if line.split() and line.split()[0] in {"OK", "CHANGED", "WARN"}]


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def test_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_fresh_root_is_set_up_and_second_run_changes_nothing(home):
    nvm = fake_nvm(home)
    (home / ".bashrc").write_text(UBUNTU_BASHRC)

    first = run(home, email="kanu@example.com")
    assert "CHANGED" in statuses(first) and "WARN" not in statuses(first), first

    assert git_config(home, "user.name") == "Kanu Madhok"
    assert git_config(home, "user.email") == "kanu@example.com"
    assert os.readlink(nvm / "current") == str(nvm / "versions/node/v22.11.0")
    for tool in TOOLS:
        link = home / ".local/bin" / tool
        assert os.readlink(link) == str(nvm / "current/bin" / tool)
        assert link.resolve() == (nvm / "versions/node/v22.11.0/bin" / tool).resolve()
    assert (home / "scratch").is_dir() and (home / "runs").is_dir()

    bashrc = (home / ".bashrc").read_text()
    assert bashrc.index("kayak-bootstrap PATH") < bashrc.index("return;;")
    assert bashrc.endswith(UBUNTU_BASHRC)
    assert "kayak-bootstrap PATH" in (home / ".profile").read_text()
    env_d = (home / ".config/environment.d/50-kayak-path.conf").read_text()
    assert f"PATH={home}/bin:{home}/.local/bin:{home}/.nvm/current/bin:" in env_d

    before = snapshot(home)
    second = run(home, email="kanu@example.com")
    assert set(statuses(second)) == {"OK"}, second
    assert snapshot(home) == before


def test_path_block_reaches_noninteractive_shell(home):
    fake_nvm(home)
    (home / ".bashrc").write_text(UBUNTU_BASHRC)
    run(home, email="kanu@example.com")
    out = subprocess.run(
        ["bash", "-c", f'source "{home}/.bashrc"; echo "$PATH"'],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
    ).stdout.strip()
    assert out.split(":")[:3] == [f"{home}/bin", f"{home}/.local/bin", f"{home}/.nvm/current/bin"]
    # Sourcing twice does not duplicate entries.
    twice = subprocess.run(
        ["bash", "-c", f'source "{home}/.bashrc"; source "{home}/.bashrc"; echo "$PATH"'],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
    ).stdout.strip()
    assert twice == out


def test_existing_identity_is_preserved(home):
    fake_nvm(home)
    (home / ".gitconfig").write_text("[user]\n\tname = Someone Else\n\temail = old@example.com\n")
    out = run(home, email="new@example.com")
    assert git_config(home, "user.name") == "Someone Else"
    assert git_config(home, "user.email") == "old@example.com"
    assert "OK      git user.name is 'Someone Else'" in out
    assert not list(home.glob(".gitconfig.bak.*"))


def test_missing_email_with_empty_identity_exits_with_the_message(home):
    fake_nvm(home)
    result = run_raw(home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "git user.email is unset" in result.stderr
    assert "--email" in result.stderr
    assert statuses(result.stdout) == []
    assert snapshot(home) == snapshot_of_fake_nvm_only(home)
    assert git_config(home, "user.name") == ""


def test_argument_wins_over_env(home):
    fake_nvm(home)
    write_gitconfig(home, "[core]\n\teditor = vim\n")
    run(home, "--email", "arg@example.com", email="env@example.com")
    assert git_config(home, "user.email") == "arg@example.com"
    assert git_config(home, "user.name") == "Kanu Madhok"
    # The pre-edit config is backed up once, though both keys were written.
    backups = list(home.glob(".gitconfig.bak.*"))
    assert len(backups) == 1 and "email" not in backups[0].read_text()


def test_email_precedence_flag_then_first_argument_then_env(home):
    fake_nvm(home)
    run(home, "first@example.com", email="env@example.com")
    assert git_config(home, "user.email") == "first@example.com"

    other = home.parent / "other"
    other.mkdir()
    run(other, "first@example.com", "--email", "flag@example.com")
    assert git_config(other, "user.email") == "flag@example.com"

    result = run_raw(other, "one@example.com", "two@example.com")
    assert result.returncode == 2
    assert "unexpected argument: two@example.com" in result.stderr


def test_email_is_not_hard_coded():
    text = SCRIPT.read_text()
    assert not [word for word in text.split() if "@" in word and "." in word.split("@")[-1]]


def test_identity_from_include_file_is_kept(home):
    fake_nvm(home)
    config = write_gitconfig(home, "[include]\n\tpath = identity.inc\n")
    included = write_included_identity(home)
    config_before, included_before = config.read_bytes(), included.read_bytes()

    out = run(home, "--email", "new@example.com")

    assert "OK      git user.name is 'Existing Name'" in out
    assert "OK      git user.email is 'existing@example.com'" in out
    assert "CHANGED git" not in out
    assert config.read_bytes() == config_before
    assert included.read_bytes() == included_before
    assert not list(home.glob(".gitconfig.bak.*"))
    assert git_config_effective(home, "user.name") == "Existing Name"
    assert git_config_effective(home, "user.email") == "existing@example.com"
    # Scope-less lookups (what git commit sees) apply includes by default.
    assert git_config_noscope(home, "user.name") == "Existing Name"
    assert git_config_noscope(home, "user.email") == "existing@example.com"
    # Nothing was copied into the top-level file either.
    assert git_config(home, "user.name") == ""


def test_identity_from_nested_include_is_kept(home):
    fake_nvm(home)
    write_gitconfig(home, "[include]\n\tpath = outer.inc\n")
    (home / "outer.inc").write_text("[include]\n\tpath = identity.inc\n")
    write_included_identity(home)
    out = run(home, "--email", "new@example.com")
    assert "CHANGED git" not in out
    assert git_config_effective(home, "user.email") == "existing@example.com"


def test_identity_keys_are_handled_independently(home):
    fake_nvm(home)
    write_gitconfig(home, "[include]\n\tpath = identity.inc\n")
    write_included_identity(home, email=None)
    out = run(home, "--email", "new@example.com")
    assert "OK      git user.name is 'Existing Name'" in out
    assert "CHANGED git user.email set to 'new@example.com'" in out
    assert git_config_effective(home, "user.name") == "Existing Name"
    assert git_config_effective(home, "user.email") == "new@example.com"

    other = home.parent / "other"
    other.mkdir()
    write_gitconfig(other, "[include]\n\tpath = identity.inc\n")
    write_included_identity(other, name=None)
    # The email is present in the include, so no address is needed here.
    out = run(other)
    assert "CHANGED git user.name set to 'Kanu Madhok'" in out
    assert "OK      git user.email is 'existing@example.com'" in out
    assert git_config_effective(other, "user.email") == "existing@example.com"


def test_matching_include_if_identity_is_kept(home):
    fake_nvm(home)
    subprocess.run(["git", "init", "-q", str(home)], check=True, capture_output=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"})
    config = write_gitconfig(home, '[includeIf "gitdir:~/"]\n\tpath = identity.inc\n')
    write_included_identity(home)
    before = config.read_bytes()
    out = run(home, "--email", "new@example.com")
    assert "OK      git user.name is 'Existing Name'" in out
    assert "OK      git user.email is 'existing@example.com'" in out
    assert config.read_bytes() == before


@pytest.mark.parametrize("include_path", ["identity.inc", "~/identity.inc", "{home}/identity.inc"],
                         ids=["relative", "tilde", "absolute"])
def test_inactive_include_if_identity_is_not_shadowed(home, include_path):
    # Elsewhere this is unset, but a value appended to the global file would
    # override the identity inside the directories the includeIf is meant for.
    fake_nvm(home)
    target = include_path.format(home=home)
    config = write_gitconfig(home, f'[includeIf "gitdir:~/my work/"]\n\tpath = {target}\n')
    write_included_identity(home)
    before = config.read_bytes()
    out = run(home, "--email", "new@example.com")
    assert "WARN    git user.name is only set by an includeIf file" in out
    assert "WARN    git user.email is only set by an includeIf file" in out
    assert "CHANGED git" not in out
    assert config.read_bytes() == before

    # And it does not count as a missing email.
    assert run_raw(home).returncode == 0


def test_inactive_include_if_without_identity_does_not_block_setting_it(home):
    fake_nvm(home)
    write_gitconfig(home, '[includeIf "gitdir:~/my work/"]\n\tpath = other.inc\n')
    (home / "other.inc").write_text("[core]\n\teditor = vim\n")
    run(home, "--email", "new@example.com")
    assert git_config_effective(home, "user.email") == "new@example.com"


def test_unreadable_include_fails_instead_of_writing(home, tmp_path):
    # git exits 128 on an include it cannot read; the identity is then unknown,
    # not empty. A stub git keeps this true for a root user.
    fake_nvm(home)
    config = write_gitconfig(home, "[include]\n\tpath = identity.inc\n")
    before = config.read_bytes()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "git").write_text("#!/bin/sh\necho 'fatal: unable to access' >&2\nexit 128\n")
    (stubs / "git").chmod(0o755)
    out = run(home, "--email", "new@example.com", returncode=1,
              path=f"{stubs}:{os.environ['PATH']}")
    assert "WARN    reading git user.name failed" in out
    assert "WARN    reading git user.email failed" in out
    assert config.read_bytes() == before


def test_empty_identity_is_set(home):
    fake_nvm(home)
    out = run(home, "--email", "new@example.com")
    assert "CHANGED git user.name set to 'Kanu Madhok'" in out
    assert "CHANGED git user.email set to 'new@example.com'" in out
    assert git_config_noscope(home, "user.name") == "Kanu Madhok"
    assert git_config_noscope(home, "user.email") == "new@example.com"


def test_second_run_is_all_ok_and_writes_nothing(home):
    fake_nvm(home)
    (home / ".bashrc").write_text(UBUNTU_BASHRC)
    write_gitconfig(home, "[include]\n\tpath = identity.inc\n")
    write_included_identity(home)
    run(home, "--email", "new@example.com")

    before = disk_state(home)
    # No email given: the identity is already effective, so none is needed.
    second = run(home)
    assert set(statuses(second)) == {"OK"}, second
    assert disk_state(home) == before


def test_wslconfig_reminder_is_printed_and_nothing_is_created(home):
    fake_nvm(home)
    out = run(home, email="kanu@example.com")
    assert "reminder" in out and ".wslconfig" in out
    assert not list(home.parent.rglob(".wslconfig"))


def test_unavailable_binaries_and_missing_nvm_warn(home):
    fake_nvm(home, tools=("node", "npm", "npx"))
    out = run(home, email="kanu@example.com")
    assert "WARN    codex is not installed" in out
    assert not (home / ".local/bin/codex").exists()
    assert (home / ".local/bin/node").is_symlink()

    bare = home.parent / "bare"
    bare.mkdir()
    out = run(bare, email="kanu@example.com")
    assert "WARN    nvm not found" in out
    assert not (bare / ".local/bin").exists()
    assert (bare / "scratch").is_dir()


@pytest.mark.parametrize("default,expected", [
    ("22", "v22.11.0"), ("v22.9.0", "v22.9.0"), ("20.18", "v20.18.0"),
    ("node", "v22.11.0"), ("lts/*", "v20.18.0"),
])
def test_nvm_default_resolution(home, default, expected):
    nvm = fake_nvm(home, default=default)
    (nvm / "alias/lts").mkdir()
    (nvm / "alias/lts/*").write_text("lts/iron\n")
    (nvm / "alias/lts/iron").write_text("v20.18.0\n")
    run(home, email="kanu@example.com")
    assert os.readlink(nvm / "current") == str(nvm / "versions/node" / expected)


def test_unresolvable_default_warns_and_skips_links(home):
    nvm = fake_nvm(home, default="18")
    out = run(home, email="kanu@example.com")
    assert "WARN    nvm default '18' does not match" in out
    assert not (nvm / "current").exists()
    assert not (home / ".local/bin").exists()


def test_stale_links_are_repointed(home):
    nvm = fake_nvm(home)
    (nvm / "current").symlink_to(nvm / "versions/node/v20.18.0")
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/node").symlink_to(home / "gone/node")
    out = run(home, email="kanu@example.com")
    assert f"CHANGED {nvm}/current -> v22.11.0" in out
    assert os.readlink(home / ".local/bin/node") == str(nvm / "current/bin/node")


def test_conflicting_real_files_are_left_alone(home):
    nvm = fake_nvm(home)
    (nvm / "current").mkdir()
    (nvm / "current/marker").write_text("mine")
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/node").write_text("#!/bin/sh\necho real node\n")
    (home / "scratch").write_text("not a dir")
    env_d = home / ".config/environment.d/50-kayak-path.conf"
    env_d.parent.mkdir(parents=True)
    env_d.write_text("PATH=/custom\n")

    out = run(home, email="kanu@example.com")
    assert f"WARN    {nvm}/current is a real file or directory" in out
    assert (nvm / "current/marker").read_text() == "mine"
    assert "WARN    scratch" not in out and f"WARN    {home}/scratch exists" in out
    assert (home / "scratch").read_text() == "not a dir"

    # A real node in .local/bin is never replaced, even once nvm resolves.
    (nvm / "current/marker").unlink()
    (nvm / "current").rmdir()
    out = run(home, email="kanu@example.com")
    assert f"WARN    {home}/.local/bin/node is a real file" in out
    assert (home / ".local/bin/node").read_text() == "#!/bin/sh\necho real node\n"
    assert (home / ".local/bin/npm").is_symlink()

    # Rewritten managed config keeps the old content in a backup.
    backups = list(env_d.parent.glob("50-kayak-path.conf.bak.*"))
    assert [b.read_text() for b in backups] == ["PATH=/custom\n"]


def test_existing_dotfiles_are_backed_up_and_misplaced_block_moves(home):
    fake_nvm(home)
    (home / ".bash_profile").write_text("source ~/.bashrc\n")
    # A block below the interactive guard never runs for ssh commands.
    (home / ".bashrc").write_text(
        UBUNTU_BASHRC + "# >>> kayak-bootstrap PATH >>>\nold\n# <<< kayak-bootstrap PATH <<<\n")
    run(home, email="kanu@example.com")

    assert not (home / ".profile").exists()
    profile = (home / ".bash_profile").read_text()
    assert profile.startswith("# >>> kayak-bootstrap PATH >>>")
    assert profile.endswith("source ~/.bashrc\n")
    bashrc = (home / ".bashrc").read_text()
    assert bashrc.count("# >>> kayak-bootstrap PATH >>>") == 1
    assert "\nold\n" not in bashrc
    assert bashrc.index("kayak-bootstrap") < bashrc.index("return;;")
    assert [b.read_text() for b in home.glob(".bash_profile.bak.*")] == ["source ~/.bashrc\n"]
    assert len(list(home.glob(".bashrc.bak.*"))) == 1


def test_symlinked_bashrc_is_edited_in_place(home):
    fake_nvm(home)
    dotfiles = home / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "bashrc").write_text(UBUNTU_BASHRC)
    (home / ".bashrc").symlink_to(dotfiles / "bashrc")
    run(home, email="kanu@example.com")
    assert (home / ".bashrc").is_symlink()
    assert "kayak-bootstrap PATH" in (dotfiles / "bashrc").read_text()


def test_symlinked_systemd_conf_target_is_backed_up(home):
    fake_nvm(home)
    target = home / "dotfiles/kayak.conf"
    target.parent.mkdir()
    target.write_text("PATH=/custom\nOTHER=1\n")
    conf = home / ".config/environment.d/50-kayak-path.conf"
    conf.parent.mkdir(parents=True)
    conf.symlink_to(target)
    run(home, email="kanu@example.com")
    assert conf.is_symlink() and "kayak/bootstrap.sh" in target.read_text()
    backups = list(target.parent.glob("kayak.conf.bak.*"))
    assert [b.read_text() for b in backups] == ["PATH=/custom\nOTHER=1\n"]


def test_xdg_git_config_is_the_file_backed_up(home):
    fake_nvm(home)
    xdg = home / ".config/git/config"
    xdg.parent.mkdir(parents=True)
    xdg.write_text("[core]\n\teditor = vim\n")
    out = run(home, email="kanu@example.com")
    assert "CHANGED git user.email set to 'kanu@example.com'" in out
    assert not (home / ".gitconfig").exists()
    assert "kanu@example.com" in xdg.read_text()
    backups = list(xdg.parent.glob("config.bak.*"))
    assert [b.read_text() for b in backups] == ["[core]\n\teditor = vim\n"]


def test_symlinked_gitconfig_target_is_backed_up(home):
    fake_nvm(home)
    target = home / "dotfiles/gitconfig"
    target.parent.mkdir()
    target.write_text("[user]\n\tname = Kanu Madhok\n")
    (home / ".gitconfig").symlink_to(target)
    run(home, email="kanu@example.com")
    assert (home / ".gitconfig").is_symlink()
    assert git_config(home, "user.email") == "kanu@example.com"
    backups = list(target.parent.glob("gitconfig.bak.*"))
    assert [b.read_text() for b in backups] == ["[user]\n\tname = Kanu Madhok\n"]


def test_locked_git_config_is_reported_and_exits_nonzero(home):
    fake_nvm(home)
    (home / ".gitconfig").write_text("[user]\n\tname = Kanu Madhok\n")
    (home / ".gitconfig.lock").write_text("")
    out = run(home, email="kanu@example.com", returncode=1)
    assert "WARN    setting git user.email failed" in out
    assert "CHANGED git user.email" not in out
    assert "1 failure(s)" in out
    assert git_config(home, "user.email") == ""


def test_failed_backup_prevents_the_edit(home, tmp_path):
    fake_nvm(home)
    conf = home / ".config/environment.d/50-kayak-path.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("PATH=/custom\n")
    # A failing cp on PATH, so this also holds for a root user, whom file
    # modes would not stop.
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "cp").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "cp").chmod(0o755)
    out = run(home, email="kanu@example.com", returncode=1,
              path=f"{stubs}:{os.environ['PATH']}")
    assert f"WARN    backup of {conf} failed" in out
    assert "CHANGED systemd user PATH" not in out
    assert conf.read_text() == "PATH=/custom\n"
    assert not list(conf.parent.glob("50-kayak-path.conf.bak.*"))


def inherited_home(tmp_path):
    real = tmp_path / "inherited"
    real.mkdir()
    (real / ".bashrc").write_text(UBUNTU_BASHRC)
    (real / ".gitconfig").write_text("[user]\n\tname = Someone Else\n")
    return real


@pytest.mark.parametrize("make_args", [
    lambda tmp: (["--root="], {}),
    lambda tmp: (["--root", ""], {}),
    lambda tmp: ([], {"KAYAK_BOOTSTRAP_ROOT": ""}),
    lambda tmp: (["--root", str(tmp / "a-file")], {}),
    lambda tmp: (["--root", str(tmp / "a-file/child")], {}),
], ids=["root-equals-empty", "root-empty-arg", "env-empty", "root-is-file", "root-under-file"])
def test_unusable_root_stops_before_touching_inherited_home(tmp_path, make_args):
    real = inherited_home(tmp_path)
    (tmp_path / "a-file").write_text("not a directory\n")
    before = snapshot(real)
    args, extra = make_args(tmp_path)
    env = {"PATH": os.environ["PATH"], "HOME": str(real), "LANG": "C",
           "GIT_EMAIL": "x@example.com", **extra}
    result = subprocess.run(["bash", str(SCRIPT), *args], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "kayak bootstrap for HOME" not in result.stdout
    assert statuses(result.stdout) == []
    assert snapshot(real) == before
    assert (tmp_path / "a-file").read_text() == "not a directory\n"


def test_no_live_host_commands():
    text = SCRIPT.read_text()
    for forbidden in ("systemctl", "loginctl", "crontab", ".exe", "apt", "curl", "npm install",
                      ".claude"):
        assert forbidden not in text, forbidden
