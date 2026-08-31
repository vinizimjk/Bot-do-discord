/**
 * RESENHA MÁXIMA — Google Forms -> ticket de candidatura no Discord
 *
 * Cole este código no Apps Script vinculado à planilha de respostas do Forms.
 * Crie um gatilho instalável para `enviarCandidaturaAoBot` ao enviar formulário.
 * Defina WEBHOOK_URL e, se usado no painel, WEBHOOK_SECRET.
 */
const WEBHOOK_URL = 'https://SEU-DOMINIO/api/candidatura-eventos';
const WEBHOOK_SECRET = '';

function enviarCandidaturaAoBot(e) {
  const respostas = {};
  const namedValues = (e && e.namedValues) ? e.namedValues : {};

  Object.keys(namedValues).forEach((pergunta) => {
    const valor = namedValues[pergunta];
    respostas[pergunta] = Array.isArray(valor) ? valor.join(', ') : String(valor || '');
  });

  // O backend procura automaticamente uma pergunta contendo "Discord" e "ID".
  const payload = { respostas: respostas };
  const headers = {};
  if (WEBHOOK_SECRET) headers['X-Webhook-Secret'] = WEBHOOK_SECRET;

  UrlFetchApp.fetch(WEBHOOK_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: headers,
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
}
