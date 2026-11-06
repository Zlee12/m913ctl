# Build from a checkout of this repo:  makepkg -si
pkgname=m913ctl
pkgver=0.3.0
pkgrel=1
pkgdesc="Configure a Redragon M913 gaming mouse's onboard buttons, macros, DPI and lighting"
arch=('any')
url="https://github.com/Zlee12/m913ctl"
license=('GPL-3.0-or-later')
depends=('python>=3.11')
optdepends=('python-gobject: m913ctl gui'
            'gtk4: m913ctl gui'
            'libadwaita: m913ctl gui'
            'python-cairo: m913ctl gui')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
source=()

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  python -m unittest discover -s tests -t .
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  install -Dm644 70-m913ctl.rules "$pkgdir/usr/lib/udev/rules.d/70-m913ctl.rules"
  install -Dm644 m913ctl.desktop "$pkgdir/usr/share/applications/m913ctl.desktop"
  install -Dm644 examples/config.toml "$pkgdir/usr/share/doc/$pkgname/config.toml"
  install -Dm644 README.md PROTOCOL.md -t "$pkgdir/usr/share/doc/$pkgname/"
}
