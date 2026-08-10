# The shell test suite

`install.sh` and `bin/trug` have no compiler and no type-checker, so this is the
only thing standing between a typo and someone's broken Saturday. It runs
against a stubbed `docker` and `curl` in a throwaway `HOME`, so it never starts
a container and finishes in a few seconds.

```sh
bats tests/shell/
```

## Run it on Linux too, before you push

macOS and Linux disagree in ways that have already shipped bugs to CI twice:

```sh
docker run --rm -v "$PWD:/repo" -w /repo ubuntu:24.04 bash -c \
  'apt-get update -qq >/dev/null && apt-get install -y -qq bats curl dash >/dev/null && bats tests/shell/'
```

Two of those disagreements are worth knowing about, because neither announces
itself:

- **bats 1.14 (Homebrew) does not fail a test when a bare `[[ ]]` returns
  non-zero anywhere but the last line. bats 1.10 (Debian, and CI) does.** A
  string assertion written as `[[ "$output" == *x* ]]` on its own line is
  therefore enforced on CI and silently ignored on a Mac. Use `assert_contains`
  and `refute_contains` from `helpers.bash` instead — they are plain functions,
  so every version catches them, and they print what they expected.
- **`stat -c` is GNU and `stat -f` is BSD**, and GNU's `-f` means
  `--file-system`, so the usual `stat -f … || stat -c …` fallback silently
  succeeds with the wrong answer on Linux. GNU first.

The suite is hermetic on purpose: `setup_sandbox` stubs the network so
`trug share` produces the same address everywhere, rather than depending on the
host having a LAN address at all. Tests that need to be root-sensitive skip
themselves when run as root.

## What is deliberately not covered

The stubs prove what the scripts *say*, never that Docker works. That is the
`install` job in CI, which installs for real against a locally-built image.
