from ..ANSI_COLORS import ANSI; C = ANSI()
from ..MODULES import IMPORT; M = IMPORT()

from .Files_Check import FileCheck

F = FileCheck(); F.Set_Path();

EX = f"{C.P}\n   |\n   ╰{C.CC}┈{C.OG}➢ {C.G}ApkPatcher {' '.join(M.sys.argv[1:])} {C.OG}"


def _is_termux():
    return bool(M.os.environ.get("TERMUX_VERSION")) or M.os.path.isdir("/data/data/com.termux/files/usr")


def _ensure_radare2():
    """Ensure the r2 executable required by r2pipe is available.

    Termux keeps the upstream pkg-based install behavior. Normal Linux/VPS
    environments never call the Termux package manager; native dependencies
    must be installed once during deployment.
    """
    r2_path = M.shutil.which("r2")
    if r2_path:
        result = M.subprocess.run(
            [r2_path, "-V"],
            stdout=M.subprocess.PIPE,
            stderr=M.subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            return

    if _is_termux():
        try:
            print(f"\n{C.S} Installing {C.E} {C.OG}➸❥ {C.G}radare2...\n")
            M.subprocess.check_call(["pkg", "install", "-y", "radare2"])
            if M.shutil.which("r2"):
                return
        except (M.subprocess.CalledProcessError, FileNotFoundError):
            pass

    exit(
        f"\n\n{C.ERROR} Radare2 (r2) is not installed or not available in PATH.  ✘\n"
        f"\n{C.INFO} On Ubuntu/Debian VPS, install Radare2 once during deployment.\n"
        f"\n{C.INFO} Verify installation with: {C.G}r2 -V\n"
    )


# ---------------- Scan APK ----------------
def Scan_Apk(apk_path, isFlutter, isPairip):

    print(f"\n{C.CC}{'_' * 61}\n")

    Package_Name = ''

    # ---------------- Extract Package Name with AAPT ----------------
    if M.os.name == 'posix':
        try:
            Package_Name = M.subprocess.run(
                ['aapt2', 'dump', 'packagename', apk_path],
                capture_output=True, text=True
            ).stdout.strip()

            if Package_Name:
                print(f"\n{C.S} Package Name {C.E} {C.OG}➸❥ {C.P}'{C.G}{Package_Name}{C.P}' {C.G} ✔")

        except Exception:
            Package_Name = ''

    # ---------------- Extract Package Name with APKEditor ----------------
    if not Package_Name:
        Package_Name = M.subprocess.run(
            ["java", "-jar", F.APKEditor_Path, "info", "-package", "-i", apk_path],
            capture_output=True, text=True
        ).stdout.split('"')[1]

        print(f"\n{C.S} Package Name {C.E} {C.OG}➸❥ {C.P}'{C.G}{Package_Name}{C.P}' {C.G} ✔")

    # ---------------- Check Flutter / Pairip Protection ----------------
    isPairip_lib = isFlutter_lib = False

    with M.zipfile.ZipFile(apk_path, 'r') as zip_ref:
        for item in zip_ref.infolist():
            if item.filename.startswith('lib/'):
                if item.filename.endswith('libpairipcore.so'):
                    isPairip_lib = True
                if item.filename.endswith('libflutter.so'):
                    isFlutter_lib = True

    # ---------------- Check Flutter Protection ----------------
    if isFlutter_lib:
        _ensure_radare2()

        FP = f"\n\n{C.S} Flutter Protection {C.E} {C.OG}➸❥ {C.P}'{C.G}libflutter.so{C.P}' {C.G} ✔"

        if not isFlutter:
            exit(
                f"{FP}\n\n"
                f"\n{C.WARN} This is Flutter APK, So For SSL Bypass , Use {C.G} -f  {C.B}Flag:\n\n"
                f"\n{C.INFO} If APK is Flutter, Then Use Additional Flag: {C.OG}-f"
                f"{EX}-f {C.Y}-c certificate.cert\n"
            )
        else:
            print(FP)

    # ---------------- Check Pairip Protection ----------------
    if isPairip_lib:
        PP = f"\n\n{C.S} Pairip Protection {C.E} {C.OG}➸❥ {C.P}'{C.G}libpairipcore.so{C.P}' {C.G} ✔"

        if not isPairip:
            exit(
                f"{PP}\n\n"
                f"\n{C.WARN} This is Pairip APK, So For SSL Bypass, Use {C.G} -p {C.C} / {C.G} -p -x  {C.C}( <isCoreX> ) {C.B}Flag:\n\n"
                f"\n{C.INFO} If APK is Pairip, Then Use Additional Flag: {C.OG}-p {C.P}( Without Sign APK Use Only in VM / Multi_App )"
                f"{EX}-p {C.Y}-c certificate.cert\n\n"
                f"\n{C.INFO} If APK is Pairip, Then Hook CoreX & Use Additional Flag: {C.OG}-p -x {C.P}( Install Directly Only For [ arm64 ] )"
                f"{EX}-p -x {C.Y}-c certificate.cert\n\n"
                f"\n{C.INFO} Note Both Method Not Stable, May be APK Crash {C.P}( So Try Your Luck ) 😂\n"
            )
        else:
            print(PP)

    return Package_Name, isFlutter_lib, isPairip_lib
