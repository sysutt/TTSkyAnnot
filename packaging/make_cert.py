"""Make the local code-signing certificate for TTSkyAnnot (the private key goes into a temporary PFX).

Why a script and not `New-SelfSignedCertificate`: that cmdlet crashes on this machine (access violation inside
CertEnroll), so the certificate is built here with the `cryptography` library — same result, no COM.

`packaging/sign.ps1 -Create` runs this once, imports the PFX into the current user's personal store, trusts the
public half for the current user, and deletes the PFX again. Nothing here is needed for a normal build.

    python packaging/make_cert.py <pfx> <cer> <password>

Prints the SHA-1 thumbprint (what signtool wants) on its last line.
"""
import datetime
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: make_cert.py <pfx> <cer> <password>")
    pfx_path, cer_path, password = sys.argv[1], sys.argv[2], sys.argv[3]

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TTSkyAnnot"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TTSkyAnnot")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))

    with open(cer_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.DER))
    pfx = pkcs12.serialize_key_and_certificates(
        name=b"TTSkyAnnot", key=key, cert=cert, cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")))
    with open(pfx_path, "wb") as fh:
        fh.write(pfx)
    print(cert.fingerprint(hashes.SHA1()).hex().upper())


if __name__ == "__main__":
    main()
