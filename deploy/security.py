#!/usr/bin/env python3
"""
security.py - read-only check of this server for signs of compromise.

    sudo python3 deploy/security.py            full check (several minutes on a slow disk)
    sudo python3 deploy/security.py --quick    skip the slow checks (package hashes, hidden pids)

What it looks for, with the focus on unknown code running on the machine:

  processes    running from deleted files or /tmp, /dev/shm; disguised as kernel threads;
               hidden from /proc (rootkit trick); heavy CPU use (cryptominers); web-user shells
  network      unexpected public listening ports; connections to miner / IRC / Tor ports
  persistence  cron, systemd units, rc.local, shell startup files, /etc/ld.so.preload,
               SSH authorized_keys, extra root accounts, Python .pth / sitecustomize hooks,
               git hooks and git config that run commands
  tampering    system package files whose contents changed (dpkg --verify), pip packages
               whose files no longer match their install records, unpackaged SUID programs
  web content  code dropped into /var/www or temp/, web shells, obfuscated code
  access       sshd settings, recent SSH logins, remote desktop software

Findings are ALERT (investigate now), WARN (review) or INFO (for context). Exit status is
2 if there were alerts, 1 for warnings only, 0 otherwise (3 if emailing the report failed).

    sudo python3 deploy/security.py --email you@example.com     also email the report
                                                                 (SMTP settings: see --help)

Nothing is changed on the system. A clean report is not proof the machine is clean: a
capable attacker with root can hide from any check run on the machine itself. If you find
real signs of compromise, the safe course is to rebuild the server from known-good media
and restore data (not code or binaries) from backups, then rotate all passwords and keys.
"""

import argparse
import base64
import glob
import hashlib
import os
import pwd
import re
import stat
import subprocess
import sys
import time

# ---------------------------------------------------------------- reporting

FINDINGS = []
LEVELS = ('ALERT', 'WARN', 'INFO')
COLORS = {'ALERT': '\033[1;31m', 'WARN': '\033[1;33m', 'INFO': '\033[36m', 'END': '\033[0m'}


def add(level, area, message, detail=None):
    FINDINGS.append((level, area, message, detail))


def run(cmd, timeout=120):
    """Run a command, return (returncode, stdout). Never raises."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, universal_newlines=True, errors='replace')
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def read(path, limit=None):
    try:
        with open(path, 'r', errors='replace') as f:
            return f.read(limit) if limit else f.read()
    except OSError:
        return None


def mtime_days(path):
    try:
        return (time.time() - os.lstat(path).st_mtime) / 86400.0
    except OSError:
        return None


def is_elf(path):
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'\x7fELF'
    except OSError:
        return False


def home_dirs():
    homes = set()
    for p in pwd.getpwall():
        if p.pw_dir and os.path.isdir(p.pw_dir) and p.pw_dir not in ('/', '/nonexistent'):
            homes.add(p.pw_dir)
    return sorted(homes)


# ---------------------------------------------------------------- package ownership

_DPKG_FILES = None


def dpkg_files():
    """Set of every path installed by a Debian package (both /bin and /usr/bin forms)."""
    global _DPKG_FILES
    if _DPKG_FILES is None:
        files = set()
        for lst in glob.glob('/var/lib/dpkg/info/*.list'):
            for line in (read(lst) or '').splitlines():
                files.add(line)
                for short in ('/bin/', '/sbin/', '/lib/', '/lib64/', '/lib32/'):
                    if line.startswith(short):
                        files.add('/usr' + line)
                    elif line.startswith('/usr' + short):
                        files.add(line[4:])
        _DPKG_FILES = files
    return _DPKG_FILES


def packaged(path):
    return path in dpkg_files() or os.path.realpath(path) in dpkg_files()


# ---------------------------------------------------------------- suspicious text patterns

# commands seen in real intrusions: download-and-run, reverse shells, miners
HIGH_CMD = re.compile(
    r'(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?(ba|da|z)?sh\b'
    r'|/dev/tcp/|/dev/udp/'
    r'|\bnc(at)?\b[^\n]*\s-[ec]\s'
    r'|\bbash\s+-i\b'
    r'|\bsocat\b[^\n]*exec'
    r'|xmrig|minerd|cpuminer|stratum\+tcp|kdevtmpfsi|kinsing'
    r'|base64\s+(-d|--decode)[^\n]*\|\s*(ba|da|z)?sh\b', re.I)
# worth a look but has legitimate uses
MED_CMD = re.compile(
    r'\bbase64\s+(-d|--decode)\b|\bpython[0-9.]*\s+-c\s|\bperl\s+-e\s|\bmkfifo\b'
    r'|/dev/shm/|(?<![\w.-])/tmp/[\w.-]*(\s|$|;)|\bchmod\s+(\+x|[0-7]*7[0-7]{2})\s+/(tmp|dev/shm|var/tmp)'
    r'|\b(curl|wget)\b[^\n]*https?://\d+\.\d+\.\d+\.\d+', re.I)

# code patterns for files under the web root
CODE_HIGH = [
    (re.compile(r'\b(exec|eval)\s*\(\s*(base64\.b64decode|codecs\.decode|zlib\.decompress|marshal\.loads|bytes\.fromhex|__import__\([\'"](base64|zlib|marshal|codecs))'),
     'executes decoded or decompressed data'),
    (re.compile(r'\beval\s*\(\s*(base64_decode|gzinflate|gzuncompress|str_rot13|hex2bin)\s*\('), 'PHP eval of decoded data'),
    (re.compile(r'\b(system|passthru|shell_exec|exec|popen|proc_open|assert|eval)\s*\(\s*\$_(GET|POST|REQUEST|COOKIE|SERVER)'),
     'PHP web shell: runs request input'),
    (re.compile(r'/dev/tcp/|stratum\+tcp|xmrig'), 'reverse shell or miner string'),
]
LONG_BLOB = re.compile(r'[A-Za-z0-9+/]{3000,}={0,2}')
CODE_EXT = ('.py', '.php', '.phtml', '.php5', '.sh', '.pl', '.cgi', '.rb', '.so')


# ---------------------------------------------------------------- checks: processes

def proc_pids():
    return sorted(int(p) for p in os.listdir('/proc') if p.isdigit())


def proc_info(pid):
    d = '/proc/%d' % pid
    info = {'pid': pid}
    try:
        info['exe'] = os.readlink(d + '/exe')
    except OSError:
        info['exe'] = None  # kernel thread, or no permission
    raw = read(d + '/cmdline') or ''
    info['cmd'] = raw.replace('\0', ' ').strip()
    stat_line = read(d + '/stat') or ''
    info['comm'] = stat_line[stat_line.find('(') + 1:stat_line.rfind(')')] if stat_line else '?'
    rest = stat_line[stat_line.rfind(')') + 2:].split()
    info['ticks'] = int(rest[11]) + int(rest[12]) if len(rest) > 12 else 0
    status = read(d + '/status') or ''
    m = re.search(r'^Uid:\s+(\d+)', status, re.M)
    uid = int(m.group(1)) if m else -1
    try:
        info['user'] = pwd.getpwuid(uid).pw_name
    except KeyError:
        info['user'] = str(uid)
    try:
        info['cwd'] = os.readlink(d + '/cwd')
    except OSError:
        info['cwd'] = None
    return info


def check_processes(quick):
    area = 'processes'
    infos = {}
    for pid in proc_pids():
        i = proc_info(pid)
        if i['comm'] != '?':
            infos[pid] = i

    bad_dirs = ('/tmp/', '/var/tmp/', '/dev/shm/', '/run/shm/')
    unpackaged = {}
    for i in infos.values():
        exe = i['exe']
        desc = 'pid %d (%s, user %s): %s' % (i['pid'], i['comm'], i['user'], i['cmd'][:200] or '[no command line]')
        if exe is None:
            continue
        if exe.endswith(' (deleted)'):
            # after a package upgrade, long-running daemons legitimately show their old binary as deleted
            real = exe[:-len(' (deleted)')]
            level = 'WARN' if packaged(real) else 'ALERT'
            add(level, area, 'process is running a program file that has been deleted', desc + '\n    exe: ' + exe +
                ('\n    (packaged program, probably just upgraded: restart the service)' if level == 'WARN' else
                 '\n    malware often deletes itself after starting'))
            continue
        if exe.startswith(bad_dirs):
            add('ALERT', area, 'process is running from a temporary directory', desc + '\n    exe: ' + exe)
        if i['cwd'] and i['cwd'].startswith(bad_dirs) and i['user'] in ('www-data', 'nobody'):
            add('WARN', area, 'web/unprivileged process working in a temporary directory', desc + '\n    cwd: ' + i['cwd'])
        # kernel threads have no executable; a user process named like one is a classic disguise
        if re.match(r'^(kworker|kthreadd|ksoftirqd|migration|rcu_|kswapd|watchdog)', i['comm']) or i['cmd'].startswith('['):
            add('ALERT', area, 'user process disguised as a kernel thread', desc + '\n    exe: ' + exe)
        if i['user'] == 'www-data' and os.path.basename(exe) in ('sh', 'bash', 'dash', 'python3', 'perl', 'nc', 'ncat', 'socat', 'curl', 'wget'):
            add('ALERT', area, 'shell or downloader running as the web server user', desc)
        if not packaged(exe):
            unpackaged.setdefault(exe, []).append(i)

    if unpackaged:
        lines = []
        for exe, procs in sorted(unpackaged.items()):
            p = procs[0]
            lines.append('%s  (%d process%s, user %s, e.g. pid %d)' % (exe, len(procs), '' if len(procs) == 1 else 'es', p['user'], p['pid']))
        add('INFO', area, 'running programs not installed by a Debian package (check you recognise each)', '\n'.join(lines))

    # CPU sample: miners show up as sustained high CPU. Take two quick snapshots around a
    # fixed sleep (the info pass above can take minutes on a busy machine, so it is not used).
    def ticks():
        t = {}
        for pid in infos:
            s = read('/proc/%d/stat' % pid)
            if s:
                rest = s[s.rfind(')') + 2:].split()
                t[pid] = int(rest[11]) + int(rest[12])
        return t
    t0, before = time.time(), ticks()
    time.sleep(3.0)
    t1, after = time.time(), ticks()
    hz = os.sysconf('SC_CLK_TCK')
    busy = []
    for pid, i in infos.items():
        if pid == os.getpid() or pid not in before or pid not in after:
            continue
        pct = (after[pid] - before[pid]) * 100.0 / (hz * (t1 - t0)) # 100% = one CPU core fully used
        if pct >= 50:
            busy.append((pct, i))
    for pct, i in sorted(busy, key=lambda x: -x[0]):
        unknown = i['exe'] is not None and not packaged(i['exe'])
        add('WARN' if unknown else 'INFO', area, 'high CPU use (%.0f%% of a core over 3 s)' % pct,
            'pid %d (%s, user %s): %s\n    exe: %s' % (i['pid'], i['comm'], i['user'], i['cmd'][:200] or '[kernel thread]', i['exe'])
            + ('\n    kswapd busy means the machine is short of memory' if i['comm'].startswith('kswapd') else ''))

    if not quick:
        check_hidden_pids()


def check_hidden_pids():
    """A rootkit that hides processes from /proc usually still lets signal 0 reach them."""
    if os.geteuid() != 0:
        add('INFO', 'processes', 'hidden process check skipped (needs root)')
        return
    listed = set(proc_pids())
    pid_max = int((read('/proc/sys/kernel/pid_max') or '32768').strip())
    suspects = []
    for pid in range(1, pid_max + 1):
        if pid in listed:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        # threads answer signal 0 but are not listed in /proc; their Tgid differs from their pid
        status = read('/proc/%d/status' % pid)
        if status:
            m = re.search(r'^Tgid:\s+(\d+)', status, re.M)
            if m and int(m.group(1)) != pid:
                continue
        suspects.append(pid)
    # recheck to rule out processes that just started or exited
    time.sleep(1.0)
    listed = set(proc_pids())
    for pid in suspects:
        if pid in listed:
            continue
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            continue
        add('ALERT', 'processes', 'process exists but is hidden from /proc (rootkit behaviour)',
            'pid %d answers signals but does not appear in the process list' % pid)


# ---------------------------------------------------------------- checks: network

EXPECTED_PUBLIC_PORTS = {22: 'ssh', 80: 'http', 443: 'https', 68: 'dhcp client', 546: 'dhcpv6 client', 5353: 'mdns'}
SUSPICIOUS_REMOTE_PORTS = {
    6667: 'IRC (botnet control)', 6697: 'IRC (botnet control)', 6666: 'IRC', 7000: 'IRC',
    3333: 'mining pool', 4444: 'mining pool / Metasploit', 5555: 'mining pool', 7777: 'mining pool',
    8888: 'mining pool', 9999: 'mining pool', 14444: 'mining pool', 14433: 'mining pool', 45700: 'mining pool',
    9050: 'Tor', 9051: 'Tor', 9001: 'Tor relay', 1080: 'SOCKS proxy', 31337: 'backdoor',
}


def split_addr(a):
    a = a.split('%')[0] if '%' in a and not a.startswith('[') else a
    host, _, port = a.rpartition(':')
    host = host.strip('[]').split('%')[0]
    try:
        return host, int(port)
    except ValueError:
        return host, None


def is_loopback(host):
    return host.startswith('127.') or host in ('::1', 'localhost') or host.startswith('::ffff:127.')


def check_network():
    area = 'network'
    rc, out = run(['ss', '-tunapH'])
    if rc is None:
        add('INFO', area, 'ss not available; network checks skipped')
        return
    outbound = []
    ephemeral_udp = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        proto, state, local, peer = parts[0], parts[1], parts[4], parts[5]
        procs = ' '.join(parts[6:])
        names = ', '.join(sorted({'%s pid %s' % m for m in re.findall(r'\("([^"]+)",pid=(\d+)', procs)})) or '?'
        lhost, lport = split_addr(local)
        phost, pport = split_addr(peer)
        if state in ('LISTEN', 'UNCONN'):
            if is_loopback(lhost) or lport is None:
                continue
            if lport in EXPECTED_PUBLIC_PORTS:
                continue
            if proto == 'udp' and lport >= 32768:
                ephemeral_udp.append('%s by %s' % (local, names))
                continue
            add('WARN', area, '%s port %d is open to the network' % (proto, lport),
                'listening on %s by %s\n    if you do not know what this is, find out before closing it' % (local, names))
        elif state == 'ESTAB' and not is_loopback(phost):
            if pport in SUSPICIOUS_REMOTE_PORTS:
                add('ALERT', area, 'connection to %s port %d (%s)' % (phost, pport, SUSPICIOUS_REMOTE_PORTS[pport]),
                    '%s %s -> %s by %s' % (proto, local, peer, names))
            # inbound connections land on our listening ports; everything else is outbound
            if lport not in EXPECTED_PUBLIC_PORTS:
                outbound.append('%-5s %-45s -> %-45s %s' % (proto, local, peer, names))
    if ephemeral_udp:
        add('INFO', area, 'UDP sockets on temporary high ports (normally video/remote-desktop or DNS clients)', '\n'.join(sorted(ephemeral_udp)))
    if outbound:
        add('INFO', area, 'outbound connections right now (check the programs are ones you expect)', '\n'.join(sorted(outbound)))


# ---------------------------------------------------------------- checks: persistence

def scan_command_text(area, path, text, what):
    from_package = packaged(path) # contents checked by dpkg --verify; only report strong signs
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if HIGH_CMD.search(s):
            add('ALERT', area, 'suspicious command in %s' % what, '%s:%d: %s' % (path, n, s[:300]))
        elif MED_CMD.search(s) and not from_package:
            add('WARN', area, 'unusual command in %s' % what, '%s:%d: %s' % (path, n, s[:300]))


def check_persistence():
    area = 'persistence'

    preload = read('/etc/ld.so.preload')
    if preload and preload.strip():
        add('ALERT', area, '/etc/ld.so.preload loads a library into every program (common rootkit method)', preload.strip())

    # cron and at
    cron_files = ['/etc/crontab'] + glob.glob('/etc/cron.d/*') + glob.glob('/var/spool/cron/crontabs/*') + glob.glob('/var/spool/cron/*')
    for d in ('/etc/cron.hourly', '/etc/cron.daily', '/etc/cron.weekly', '/etc/cron.monthly'):
        for f in glob.glob(d + '/*'):
            if os.path.basename(f) != '.placeholder' and not packaged(f) and not re.search(r'\.dpkg-(remove|old|dist|bak|new)$', f):
                add('WARN', area, 'cron script not installed by a package', f)
            cron_files.append(f)
    user_entries = []
    for f in cron_files:
        if not os.path.isfile(f):
            continue
        text = read(f)
        if text is None:
            if os.geteuid() != 0:
                continue
            text = ''
        scan_command_text(area, f, text, 'a cron job')
        if f.startswith('/var/spool/cron') or (f.startswith('/etc/cron.d/') and not packaged(f)):
            for line in text.splitlines():
                if line.strip() and not line.strip().startswith('#') and not re.match(r'^\s*\w+=', line):
                    user_entries.append('%s: %s' % (f, line.strip()[:250]))
    if user_entries:
        add('INFO', area, 'cron jobs added by users or admins', '\n'.join(user_entries))
    at = [f for f in glob.glob('/var/spool/cron/atjobs/*') if not os.path.basename(f).startswith('.')]
    if at:
        add('WARN', area, 'scheduled "at" jobs exist', '\n'.join(at))

    # systemd units
    unit_dirs = ['/etc/systemd/system', '/usr/local/lib/systemd/system', '/run/systemd/system'] + \
                [h + '/.config/systemd/user' for h in home_dirs()]
    custom = []
    seen_units = set()
    for d in unit_dirs:
        for f in glob.glob(d + '/**/*', recursive=True):
            if not re.search(r'\.(service|timer|socket|path|conf)$', f) or os.path.isdir(f):
                continue
            if os.path.islink(f):
                target = os.path.realpath(f)
                if packaged(target) or target == '/dev/null':
                    continue
                f = target
            if packaged(f) or f in seen_units:
                continue
            seen_units.add(f)
            text = read(f) or ''
            execs = [l.strip() for l in text.splitlines() if re.match(r'\s*Exec\w*\s*=', l)]
            custom.append('%s\n      %s' % (f, '\n      '.join(execs) if execs else '(no Exec lines)'))
            scan_command_text(area, f, '\n'.join(execs), 'a systemd unit')
            for e in execs:
                prog = e.split('=', 1)[1].strip().lstrip('-@!+:').split()
                if prog and prog[0].startswith(('/tmp/', '/var/tmp/', '/dev/shm/')):
                    add('ALERT', area, 'systemd unit runs a program from a temporary directory', '%s: %s' % (f, e))
    if custom:
        add('INFO', area, 'systemd units not installed by a package (check you recognise each)', '\n'.join(custom))

    rc = read('/etc/rc.local')
    if rc:
        cmds = [l for l in rc.splitlines() if l.strip() and not l.strip().startswith('#') and l.strip() not in ('exit 0',)]
        if cmds:
            add('INFO', area, '/etc/rc.local runs commands at boot', '\n'.join(cmds))
        scan_command_text(area, '/etc/rc.local', rc, '/etc/rc.local')

    # shell startup files
    startup = ['/etc/profile', '/etc/bash.bashrc', '/etc/environment'] + glob.glob('/etc/profile.d/*')
    for h in home_dirs():
        for n in ('.bashrc', '.profile', '.bash_profile', '.bash_login', '.bash_logout', '.zshrc', '.zprofile'):
            startup.append(os.path.join(h, n))
    for f in startup:
        text = read(f)
        if text:
            scan_command_text(area, f, text, 'a shell startup file')
            if re.search(r'^\s*(export\s+)?LD_PRELOAD=', text, re.M):
                add('ALERT', area, 'shell startup file sets LD_PRELOAD', f)
            # alias ls='ls --color=auto' is normal; alias ls='/tmp/x' is not
            for m in re.finditer(r'^\s*alias\s+(sudo|ssh|su|ls|ps|netstat|ss|top|lsof|find)=([\'"]?)(\S+)', text, re.M):
                if os.path.basename(m.group(3).strip('\'"')) != m.group(1):
                    add('WARN', area, 'shell startup file redefines a security-relevant command', '%s: %s' % (f, m.group(0).strip()))

    # accounts and SSH keys
    for p in pwd.getpwall():
        if p.pw_uid == 0 and p.pw_name != 'root':
            add('ALERT', area, 'extra account with root privileges (uid 0)', p.pw_name)
    for h in home_dirs():
        for f in glob.glob(h + '/.ssh/authorized_keys*'):
            text = read(f)
            if text is None:
                continue
            keys = [l for l in text.splitlines() if l.strip() and not l.startswith('#')]
            age = mtime_days(f)
            detail = '%s (%d key%s, changed %.0f days ago)\n      %s' % (
                f, len(keys), '' if len(keys) == 1 else 's', age or 0,
                '\n      '.join((k.split()[-1] if len(k.split()) > 2 else k.split()[0]) + ('  [has command= option]' if 'command=' in k.split()[0] else '') for k in keys))
            add('WARN' if age is not None and age < 14 else 'INFO', area,
                'SSH keys that can log in' + (' (file changed in the last 14 days)' if age is not None and age < 14 else ''), detail)
    sudoers = []
    for f in ['/etc/sudoers'] + glob.glob('/etc/sudoers.d/*'):
        for line in (read(f) or '').splitlines():
            if 'NOPASSWD' in line and not line.strip().startswith('#'):
                sudoers.append('%s: %s' % (f, line.strip()))
    if sudoers:
        add('INFO', area, 'accounts that can use sudo without a password', '\n'.join(sudoers))

    check_python_hooks()
    check_git_repos_hooks()


def site_package_dirs():
    dirs = set(glob.glob('/usr/lib/python3*/site-packages') + glob.glob('/usr/lib/python3/dist-packages') +
               glob.glob('/usr/lib/python3*/dist-packages') + glob.glob('/usr/local/lib/python3*/dist-packages') +
               glob.glob('/usr/local/lib/python3*/site-packages'))
    for h in home_dirs():
        dirs.update(glob.glob(h + '/.local/lib/python3*/site-packages'))
    return sorted(d for d in dirs if os.path.isdir(d))


def check_python_hooks():
    """.pth files and sitecustomize run code every time Python starts: a stealthy persistence spot."""
    area = 'persistence'
    for d in site_package_dirs():
        for f in glob.glob(d + '/*.pth'):
            text = read(f) or ''
            code = [l for l in text.splitlines() if l.startswith(('import ', 'import\t'))]
            if not code:
                continue
            if any(re.search(r'base64|exec\(|eval\(|urllib|socket|subprocess|marshal|zlib|compile\(', l) for l in code):
                add('ALERT', area, 'Python .pth file runs suspicious code at every Python start', '%s\n      %s' % (f, '\n      '.join(c[:250] for c in code)))
            elif not packaged(f):
                add('INFO', area, 'Python .pth file runs code at every Python start (usually from a pip package)', '%s\n      %s' % (f, '\n      '.join(c[:250] for c in code)))
        for n in ('sitecustomize.py', 'usercustomize.py'):
            f = os.path.join(d, n)
            if os.path.exists(f) and not packaged(f):
                add('WARN', area, '%s runs at every Python start and was not installed by a package' % n, f)


def find_git_repos(root='/var/www', max_depth=4):
    repos = []
    for dirpath, dirnames, _ in os.walk(root):
        if dirpath.count(os.sep) - root.count(os.sep) >= max_depth:
            dirnames[:] = []
        if '.git' in dirnames:
            repos.append(dirpath)
            dirnames.remove('.git')
    return repos


def check_git_repos_hooks():
    """git hooks and some config keys run commands whenever git is used in that repo."""
    area = 'persistence'
    for repo in find_git_repos():
        hooks = [f for f in glob.glob(repo + '/.git/hooks/*') if not f.endswith('.sample')]
        if hooks:
            add('WARN', area, 'git hooks run whenever git is used in %s' % repo, '\n'.join(hooks))
        cfg = read(repo + '/.git/config') or ''
        for line in cfg.splitlines():
            if re.match(r'\s*(fsmonitor|hookspath|sshcommand|pager|editor)\s*=', line, re.I) or re.match(r'\s*\w+\s*=\s*!', line):
                add('WARN', area, 'git config in %s runs a command' % repo, line.strip())


# ---------------------------------------------------------------- checks: tampering

def check_dpkg_verify():
    area = 'tampering'
    rc, out = run(['dpkg', '--verify'], timeout=1800)
    if rc is None:
        add('INFO', area, 'dpkg --verify did not run (not installed or timed out)')
        return
    changed = []
    for line in out.splitlines():
        m = re.match(r'^(\S{9})\s+(c\s+)?(/.*)$', line)
        if not m or m.group(1) == 'missing':
            continue
        flags, is_conf, path = m.group(1), bool(m.group(2)), m.group(3)
        if flags[2] != '5' or is_conf:
            continue  # only contents changed (checksum) on non-config files matter here
        changed.append(path)
    code_dirs = ('/bin/', '/sbin/', '/usr/bin/', '/usr/sbin/', '/lib/', '/usr/lib/', '/lib64/', '/usr/libexec/', '/etc/')
    for path in changed:
        if path.startswith(code_dirs) and not path.startswith(('/usr/lib/python3/dist-packages/', '/usr/share/')):
            add('ALERT', area, 'system program or library differs from its package', path +
                '\n    reinstall the package to compare:  dpkg -S %s' % path)
        else:
            add('WARN', area, 'packaged file differs from its package', path)


def b64_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return base64.urlsafe_b64encode(h.digest()).rstrip(b'=').decode()


def matches_record(path, recorded):
    """True if the file matches pip's recorded hash. Older pip versions recorded scripts
    with a '#!python' first line and then rewrote it to the real interpreter path, so a
    script that differs only in its #! line is unchanged."""
    if b64_sha256(path) == recorded:
        return True
    with open(path, 'rb') as f:
        data = f.read(4 * 1024 * 1024)
    if data.startswith(b'#!') and b'\n' in data:
        rest = data.split(b'\n', 1)[1]
        digest = hashlib.sha256(b'#!python\n' + rest).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode() == recorded
    return False


def check_pip_records():
    """pip records a hash of every file it installs; compare them to catch modified packages."""
    area = 'tampering'
    dirs = [d for d in site_package_dirs() if d.startswith(('/usr/local/', '/home/', '/root/'))]
    unverifiable = []
    for d in dirs:
        for dist in sorted(glob.glob(d + '/*.dist-info')):
            record = read(dist + '/RECORD')
            if record is None:
                continue
            listed = set()
            tops = set()
            for line in record.splitlines():
                parts = line.rsplit(',', 2)
                if len(parts) != 3:
                    continue
                rel, digest, _ = parts
                full = os.path.normpath(os.path.join(d, rel))
                listed.add(full)
                if not rel.startswith('..') and '.dist-info' not in rel.split('/')[0]:
                    tops.add(rel.split('/')[0])
                if not digest.startswith('sha256=') or not os.path.isfile(full):
                    continue
                try:
                    if not matches_record(full, digest[7:]):
                        level = 'ALERT' if full.endswith(('.py', '.so', '.pth')) else 'WARN'
                        add(level, area, 'pip package file changed since it was installed (%s)' % os.path.basename(dist), full)
                except OSError:
                    pass
            # code files added inside a package's directory after installation
            for top in tops:
                topdir = os.path.join(d, top)
                if not os.path.isdir(topdir):
                    continue
                for dirpath, dirnames, filenames in os.walk(topdir):
                    dirnames[:] = [x for x in dirnames if x != '__pycache__']
                    for fn in filenames:
                        full = os.path.join(dirpath, fn)
                        if fn.endswith(('.py', '.so', '.pth', '.sh')) and full not in listed:
                            add('WARN', area, 'code file in a pip package that pip did not install (%s)' % os.path.basename(dist), full)
        for egg in glob.glob(d + '/*.egg-info'):
            unverifiable.append(egg)
    if unverifiable:
        add('INFO', area, 'packages installed without hash records (cannot be verified)', '\n'.join(unverifiable))


def check_suid():
    area = 'tampering'
    rc, out = run(['find', '/', '-xdev', '-type', 'f', '(', '-perm', '-4000', '-o', '-perm', '-2000', ')'], timeout=900)
    for path in out.splitlines():
        if not packaged(path):
            add('ALERT', area, 'SUID/SGID program not installed by a package (can be used to regain root)', path)


def check_kernel():
    area = 'tampering'
    t = (read('/proc/sys/kernel/tainted') or '0').strip()
    if t != '0':
        reasons = {0: 'proprietary module', 12: 'out-of-tree module', 13: 'unsigned module', 15: 'kernel live patch'}
        bits = [reasons.get(b, 'flag %d' % b) for b in range(18) if int(t) >> b & 1]
        add('WARN' if 12 in [b for b in range(18) if int(t) >> b & 1] or 13 in [b for b in range(18) if int(t) >> b & 1] else 'INFO',
            area, 'kernel is tainted (%s)' % ', '.join(bits), 'value %s; check lsmod for modules you do not recognise' % t)
    rel = os.uname().release
    for line in (read('/proc/modules') or '').splitlines():
        name = line.split()[0]
        rc, out = run(['modinfo', '-n', name], timeout=20)
        path = out.strip()
        if rc == 0 and path and path != '(builtin)' and not packaged(path) and '/updates/dkms/' not in path:
            add('WARN', area, 'loaded kernel module not installed by a package', '%s: %s' % (name, path))
        elif rc not in (0, None):
            add('WARN', area, 'loaded kernel module has no file on disk for kernel %s' % rel, name)


# ---------------------------------------------------------------- checks: web content

def check_web(project_temp):
    area = 'web content'
    if not os.path.isdir('/var/www'):
        return
    php_files = []
    for dirpath, dirnames, filenames in os.walk('/var/www'):
        dirnames[:] = [d for d in dirnames if d not in ('.git', 'node_modules', '__pycache__')]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.realpath(full) == os.path.realpath(__file__):
                continue # this script contains the patterns it searches for
            low = fn.lower()
            if low.endswith(('.php', '.phtml', '.php5')):
                php_files.append(full)
            if not low.endswith(CODE_EXT[:-1]):
                continue
            try:
                if os.path.getsize(full) > 5 * 1024 * 1024:
                    continue
            except OSError:
                continue
            text = read(full) or ''
            for rx, why in CODE_HIGH:
                m = rx.search(text)
                if m:
                    line = text.count('\n', 0, m.start()) + 1
                    add('ALERT', area, 'possible malicious code: %s' % why, '%s:%d: %s' % (full, line, text.splitlines()[line - 1].strip()[:200]))
            if ('socket' in text and 'connect(' in text and
                    re.search(r'pty\.spawn|[\'"]/bin/(ba)?sh[\'"]|subprocess\.call\(\s*\[\s*[\'"]/bin/', text) and
                    re.search(r'os\.dup2|fileno\(\)', text)):
                add('ALERT', area, 'possible reverse shell (socket connected to a shell)', full)
            if LONG_BLOB.search(text):
                add('WARN', area, 'very long encoded string in a code file (often hides a payload)', full)
    # this site has no PHP; PHP files under the web root are a classic dropped web shell
    if php_files and not run(['sh', '-c', 'command -v php || ls /etc/php'])[1].strip():
        add('WARN', area, 'PHP files under /var/www although PHP is not installed', '\n'.join(php_files[:50]))

    # files the site writes: temp/ should only ever hold graphs, pages, PDFs and logs
    if project_temp and os.path.isdir(project_temp):
        for dirpath, dirnames, filenames in os.walk(project_temp):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                try:
                    mode = os.lstat(full).st_mode
                except OSError:
                    continue
                if fn.lower().endswith(CODE_EXT) or is_elf(full) or (stat.S_ISREG(mode) and mode & 0o111 and not fn.lower().endswith(('.png', '.js', '.ico', '.gif', '.html', '.svg', '.pdf'))):
                    add('ALERT', area, 'code or executable in the site\'s temp directory', full)

    # code in git repos that git does not know about
    for repo in find_git_repos():
        rc, out = run(['git', '-c', 'safe.directory=*', '-C', repo, 'status', '--porcelain', '--untracked-files=all'], timeout=120)
        if rc != 0:
            continue
        new = [l[3:] for l in out.splitlines() if l.startswith('??') and l[3:].lower().endswith(CODE_EXT)]
        if new:
            add('WARN', area, 'code files in %s that are not in git (check you wrote them)' % repo, '\n'.join(new[:50]))

    # programs in temp directories
    for d in ('/tmp', '/var/tmp', '/dev/shm'):
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames if not x.startswith('systemd-private-')]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if is_elf(full):
                    add('ALERT', area, 'program file in a temporary directory', full)
                elif fn.endswith(('.sh', '.py', '.pl')) and full.startswith('/dev/shm'):
                    add('WARN', area, 'script in /dev/shm', full)


# ---------------------------------------------------------------- checks: access

def check_access():
    area = 'access'
    rc, out = run(['sshd', '-T'], timeout=30)
    if rc == 0:
        cfg = dict(l.split(None, 1) for l in out.splitlines() if ' ' in l)
        if cfg.get('permitrootlogin') == 'yes':
            add('WARN', area, 'SSH allows root to log in with a password', 'set PermitRootLogin prohibit-password (or no)')
        if cfg.get('passwordauthentication') == 'yes':
            add('WARN', area, 'SSH accepts passwords (keys only is much safer against brute force)', 'PasswordAuthentication yes')
    rc, out = run(['journalctl', '--since', '14 days ago', '-o', 'short-iso', '--no-pager', '-t', 'sshd', '-t', 'sshd-session'], timeout=180)
    accepted, failed = {}, 0
    for line in out.splitlines():
        m = re.search(r'Accepted (\S+) for (\S+) from (\S+)', line)
        if m:
            key = '%s from %s (%s)' % (m.group(2), m.group(3), m.group(1))
            accepted.setdefault(key, [0, line[:19]])
            accepted[key][0] += 1
            accepted[key][1] = line[:19]
        elif re.search(r'Failed password|Invalid user', line):
            failed += 1
    if accepted:
        add('INFO', area, 'successful SSH logins in the last 14 days (check every address is yours)',
            '\n'.join('%s: %d login%s, last %s' % (k, v[0], '' if v[0] == 1 else 's', v[1]) for k, v in sorted(accepted.items())))
    if failed:
        add('INFO', area, '%d failed SSH login attempts in the last 14 days (normal background noise on the internet)' % failed)
    for f in ('/opt/google/chrome-remote-desktop', '/usr/bin/anydesk', '/usr/bin/teamviewer', '/usr/sbin/xrdp', '/usr/bin/x11vnc'):
        if os.path.exists(f):
            add('INFO', area, 'remote desktop software installed: %s (another way in; keep its PIN/password strong or remove it)' % f)


# ---------------------------------------------------------------- main

EMAIL_CONFIG_HELP = """\
[smtp]
host = smtp.gmail.com
port = 587            ; 465 for SSL, anything else uses STARTTLS
user = findcurves@gmail.com
password = xxxxxxxxxxxxxxxx   ; for Gmail: a 16-letter app password, not the account password
from = findcurves@gmail.com
"""


def load_email_config(path):
    """Read SMTP settings. When running as root the file must belong to root and be private,
    otherwise anyone who can edit it could redirect or read the reports."""
    import configparser
    st = os.stat(path)
    if os.geteuid() == 0 and (st.st_uid != 0 or st.st_mode & 0o077):
        raise RuntimeError('%s must be owned by root with mode 600 (chown root:root, chmod 600)' % path)
    cp = configparser.ConfigParser(inline_comment_prefixes=(';',))
    cp.read(path)
    c = cp['smtp']
    return {'host': c['host'], 'port': int(c.get('port', '587')), 'user': c['user'],
            'password': c['password'], 'from': c.get('from', c['user'])}


def send_report(cfg, to_addr, subject, body):
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = cfg['from'], to_addr, subject
    msg.set_content(body)
    if cfg['port'] == 465:
        conn = smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=60)
    else:
        conn = smtplib.SMTP(cfg['host'], cfg['port'], timeout=60)
        conn.starttls()
    with conn:
        conn.login(cfg['user'], cfg['password'])
        conn.send_message(msg)


def format_report(color):
    out = []
    for level in LEVELS:
        items = [f for f in FINDINGS if f[0] == level]
        if not items:
            continue
        head = '%s (%d)' % (level, len(items))
        out.append((COLORS[level] + head + COLORS['END']) if color else head)
        out.append('=' * len(head))
        for _, area, msg, detail in items:
            out.append('[%s] %s' % (area, msg))
            if detail:
                out.append('    ' + detail.replace('\n', '\n    '))
        out.append('')
    n = {l: sum(1 for f in FINDINGS if f[0] == l) for l in LEVELS}
    out.append('Summary: %d alert(s), %d warning(s), %d info.' % (n['ALERT'], n['WARN'], n['INFO']))
    if n['ALERT']:
        out.append('Investigate every ALERT. If one is real, take the server offline and rebuild it rather than cleaning it.')
    out.append('A clean result is not proof the machine is clean; consider also: debsums, rkhunter, chkrootkit, ClamAV.')
    return '\n'.join(out), n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--quick', action='store_true', help='skip dpkg --verify, pip hash checks, SUID scan and hidden pid scan')
    ap.add_argument('--no-color', action='store_true')
    ap.add_argument('--project-temp', default='/var/www/html/zunzunsite3/temp', help='the site\'s temp directory')
    ap.add_argument('--email', metavar='ADDRESS', help='email the report to this address')
    ap.add_argument('--email-on', choices=('always', 'problems', 'alerts'), default='always',
                    help='when to send: always (default), problems (any WARN or ALERT), alerts (ALERT only)')
    ap.add_argument('--email-config', default='/etc/zunzunsite3/security_email.conf',
                    help='SMTP settings file, format:\n' + EMAIL_CONFIG_HELP)
    args = ap.parse_args()
    color = sys.stdout.isatty() and not args.no_color

    email_cfg = None
    if args.email:  # check the mail settings before spending 15 minutes on the scan
        try:
            email_cfg = load_email_config(args.email_config)
        except (OSError, KeyError, ValueError, RuntimeError) as e:
            print('Cannot email the report: %s\nThe settings file should look like:\n%s' % (e, EMAIL_CONFIG_HELP), file=sys.stderr)
            sys.exit(3)

    if os.geteuid() != 0:
        print('Not running as root: many checks will be incomplete. Run:  sudo python3 %s\n' % sys.argv[0])

    steps = [
        ('processes', lambda: check_processes(args.quick)),
        ('network', check_network),
        ('persistence', check_persistence),
        ('kernel', check_kernel),
        ('web content', lambda: check_web(args.project_temp)),
        ('access', check_access),
    ]
    if not args.quick:
        steps += [('installed packages (dpkg --verify, slow)', check_dpkg_verify),
                  ('pip packages', check_pip_records),
                  ('SUID programs', check_suid)]
    started = time.strftime('%Y-%m-%d %H:%M:%S %Z')
    for name, fn in steps:
        print('checking %s ...' % name, file=sys.stderr, flush=True)
        try:
            fn()
        except Exception as e:  # one broken check must not stop the others
            add('WARN', name, 'check failed to run: %r' % e)

    report, n = format_report(color)
    print()
    print(report)

    if email_cfg:
        wanted = {'always': True, 'problems': bool(n['ALERT'] or n['WARN']), 'alerts': bool(n['ALERT'])}[args.email_on]
        if wanted:
            host = os.uname().nodename
            subject = '[%s security] %d alert(s), %d warning(s)' % (host, n['ALERT'], n['WARN'])
            body = 'Security check of %s, started %s%s.\n\n%s\n' % (host, started, ' (quick)' if args.quick else '', format_report(False)[0])
            try:
                send_report(email_cfg, args.email, subject, body)
                print('\nReport emailed to %s' % args.email)
            except Exception as e:
                print('\nEmailing the report to %s failed: %r' % (args.email, e), file=sys.stderr)
                sys.exit(3)
    sys.exit(2 if n['ALERT'] else 1 if n['WARN'] else 0)


if __name__ == '__main__':
    main()
