from flask import Flask, jsonify
import imaplib
import email
import base64
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

@app.route("/buscar-emails", methods=["GET"])
def buscar_emails():
    mail = imaplib.IMAP4_SSL(os.getenv("IMAP_HOST"))
    mail.login(os.getenv("IMAP_USER"), os.getenv("IMAP_PASS"))
    mail.select("inbox")

    result, data = mail.search(None, "UNSEEN")

    if result != "OK":
        mail.logout()
        return jsonify({"erro": "Falha ao buscar emails"}), 500

    documentos = []

    for num in data[0].split():
        result, msg_data = mail.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])

        remetente = msg["from"]
        assunto = msg["subject"]
        data_email = msg["date"]

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue

            filename = part.get_filename()
            if not filename:
                continue

            conteudo = part.get_payload(decode=True)
            mime = part.get_content_type()

            documentos.append({
                "remetente": remetente,
                "assunto": assunto,
                "dataEmail": data_email,
                "nomeArquivo": filename,
                "tipoArquivo": mime,
                "base64": base64.b64encode(conteudo).decode("utf-8")
            })

    mail.logout()
    return jsonify(documentos)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)