# DATA PROCESSING AGREEMENT (DPA) — TELECOM-TOWER-POWER

> **Resumo executivo / template.** Este documento descreve, em alto nível,
> o regime de tratamento de dados pessoais aplicável ao serviço hospedado
> TELECOM-TOWER-POWER, em conformidade com a **Lei nº 13.709/2018 (LGPD)**.
> Para clientes Enterprise ou contratos com volume relevante de dados
> pessoais, recomenda-se a celebração de DPA assinado, que prevalece sobre
> este resumo em caso de conflito.

Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER").
All rights reserved.

> **Nota sobre o Operador.** Enquanto a constituição de pessoa jurídica
> está em curso, o Operador é **Daniel Azevedo Novais**, pessoa física,
> operando sob o nome empresarial "TELECOM-TOWER-POWER". A condição de
> Operador (Art. 5º VII LGPD) será automaticamente assumida pela PJ após
> sua constituição, com notificação ao Controlador.

## 1. Definições

Os termos abaixo seguem o Art. 5º da LGPD:

- **Controlador (Controller)**: o **Cliente** (Licenciado), responsável
  por decisões sobre tratamento dos dados pessoais que insere no
  serviço.
- **Operador (Processor)**: **TELECOM-TOWER-POWER**, que trata dados
  pessoais em nome e por instrução do Controlador.
- **Suboperador (Sub-processor)**: terceiros contratados pelo Operador
  para apoiar a prestação do serviço (ex.: provedores de nuvem).
- **Titular**: pessoa natural a quem se referem os dados pessoais.
- **ANPD**: Autoridade Nacional de Proteção de Dados.
- **Incidente de Segurança**: evento adverso que afete a
  confidencialidade, integridade ou disponibilidade dos dados pessoais.

## 2. Papéis

| Tipo de dado | Papel TELECOM-TOWER-POWER |
|---|---|
| Dados pessoais inseridos pelo Cliente via API/UI (ex.: receivers, contatos) | **Operador** |
| Dados de cadastro do próprio Cliente (representante legal, faturamento, suporte) | **Controlador** |
| Dados de telemetria operacional do serviço (logs técnicos, métricas) | **Controlador** (interesse legítimo) |
| Dados ANATEL/IBGE/SRTM/OpenCellID | Não constituem dados pessoais |

## 3. Objeto e finalidade do tratamento

O Operador tratará dados pessoais exclusivamente para:

- Prestar o serviço SaaS contratado (análise de enlace, cobertura,
  relatórios PDF, planejamento de repetidoras, chat Bedrock).
- Suporte técnico e operacional ao Cliente.
- Faturamento e gestão contratual.
- Cumprimento de obrigações legais e regulatórias.
- Segurança da informação, prevenção a fraude e auditoria de uso.

## 4. Categorias de dados e titulares

Tratados por instrução do Controlador, conforme uso do serviço:

- **Categorias típicas**: nome, e-mail, telefone, identificação interna
  do Cliente, coordenadas geográficas associadas a "receivers"
  (potenciais clientes finais), histórico de relatórios gerados.
- **Categorias sensíveis**: o serviço **não é destinado** ao tratamento
  de dados pessoais sensíveis (Art. 11 LGPD). O Cliente compromete-se a
  não inserir tais dados.
- **Crianças e adolescentes**: o serviço não é destinado a este público.
- **Titulares**: representantes do Cliente, usuários nomeados pelo
  Cliente, e contatos finais cadastrados pelo Cliente como receivers.

## 5. Base legal

O Cliente, na qualidade de Controlador, declara possuir base legal
adequada (Art. 7º ou Art. 11 LGPD) para o tratamento dos dados pessoais
que insere no serviço, incluindo, quando aplicável: execução de
contrato, legítimo interesse, cumprimento de obrigação legal ou
consentimento.

O Operador atua **exclusivamente sob instrução documentada** do
Controlador.

## 6. Suboperadores

### 6.1 Lista atual

| Suboperador | Finalidade | Localização primária | Salvaguardas |
|---|---|---|---|
| Amazon Web Services (AWS) | Infraestrutura (ECS Fargate, RDS, S3, Lambda, SQS, Cognito) | sa-east-1 (São Paulo) | Cláusulas DPA AWS; ISO 27001/27017/27018, SOC 2 |
| Amazon Bedrock | Inferência LLM | **us-east-1 (EUA)** — vide Seção 8 sobre transferência internacional | Cláusulas DPA AWS; *no model training* on customer data |
| Stripe Payments | Faturamento e cobrança | Estados Unidos / Irlanda | Cláusulas DPA Stripe; PCI-DSS Nível 1 |
| Railway (failover) | Hospedagem warm-failover | Region declarada no plano | Acordo de nível operador |
| GitHub | CI/CD, build, deploy artefatos | Estados Unidos | DPA GitHub; SOC 2 |
| PagerDuty / Slack | Notificação operacional (alertas, sem dados de titular) | Estados Unidos | Apenas metadados técnicos |

### 6.2 Notificação e objeção

- O Operador notificará o Controlador com antecedência razoável (mínimo
  30 dias) sobre **inclusão ou substituição** de suboperador.
- O Controlador poderá objetar de forma motivada; persistindo o impasse,
  qualquer das partes poderá rescindir sem ônus a parcela do contrato
  afetada.
- Atualizações da lista são publicadas em
  <https://telecomtowerpower.com.br/legal/subprocessors>.

## 7. Medidas de segurança

O Operador implementa medidas técnicas e organizacionais compatíveis com
o estado da arte:

- **Criptografia em trânsito**: TLS 1.2+ em todos os endpoints (ALB,
  Caddy, Bedrock, RDS).
- **Criptografia em repouso**: AES-256 em RDS, S3, EBS e snapshots.
- **Controle de acesso**: SSO/OIDC (Cognito), API keys com escopo,
  princípio do menor privilégio, MFA para acesso administrativo.
- **Segregação de tenants**: API keys por cliente, audit log por
  tenant em toda ação.
- **Logs de auditoria**: tabela `audit_log` com retenção mínima de 12
  meses; logs de aplicação centralizados.
- **Backups**: PostgreSQL e Grafana com snapshot diário em S3
  (retenção 14 dias) e **drill semanal de restauração** verificada.
- **Gestão de vulnerabilidades**: scanners no CI (license-scan,
  dependabot/equivalente), patching de imagens base.
- **Resposta a incidentes**: runbook publicado em [SECURITY.md](SECURITY.md).
- **Privacy by design / by default**: minimização de dados em logs e
  telemetria.

## 8. Transferência internacional

- O processamento primário ocorre em **AWS sa-east-1 (São Paulo)**.
- Transferências internacionais ocorrerão somente quando indispensáveis
  ao serviço (ex.: Bedrock em region distinta, Stripe nos EUA), com base
  no Art. 33 LGPD, mediante:
  - cláusulas contratuais padrão acordadas com o suboperador, ou
  - decisão de adequação aplicável, ou
  - garantias específicas pactuadas com o Controlador.
- O Controlador será informado das transferências relevantes.

## 9. Direitos dos titulares

O Operador apoiará o Controlador, dentro de prazos razoáveis e mediante
as funcionalidades disponíveis, no atendimento de requisições de
titulares (Art. 18 LGPD): confirmação, acesso, correção, anonimização,
portabilidade, eliminação, informações sobre compartilhamento,
revogação de consentimento.

Solicitações de titulares devem ser endereçadas ao **Controlador**, que
é o ponto único de contato com o titular, salvo determinação legal em
contrário.

## 10. Retenção e eliminação

- Dados do Cliente são retidos enquanto vigorar o contrato.
- Após o término, os dados serão **eliminados** ou **devolvidos** em
  formato estruturado, conforme instrução do Controlador, em até **90
  dias**.
- Cópias em backups serão eliminadas no ciclo natural de rotação (até
  14 dias após a última retenção primária).
- Audit logs e dados estritamente necessários ao cumprimento de
  obrigação legal poderão ser retidos pelos prazos legais aplicáveis
  (ex.: faturamento, normas tributárias).

## 11. Notificação de incidentes

- O Operador notificará o Controlador, sem demora injustificada e em até
  **48 (quarenta e oito) horas** após a detecção, sobre incidente de
  segurança envolvendo dados pessoais sob seu tratamento.
- A notificação conterá, na medida do conhecimento disponível: descrição
  da natureza do incidente, categorias e número aproximado de titulares
  e registros, possíveis consequências, medidas tomadas ou propostas.
- O Operador cooperará com o Controlador na investigação, remediação e,
  quando cabível, comunicação à ANPD e aos titulares (Art. 48 LGPD).

## 12. Auditoria

- Para clientes Enterprise, auditoria documental anual ou mediante
  solicitação razoável e justificada, sujeita a:
  - notificação prévia de no mínimo 30 dias,
  - NDA mútuo,
  - escopo previamente acordado por escrito,
  - custos do auditor a cargo do Controlador, salvo se houver
    descumprimento material confirmado pela auditoria.
- Auditorias on-site não serão necessárias quando o Operador puder
  comprovar o cumprimento por meio de relatórios de auditoria
  independentes (ex.: SOC 2 dos suboperadores) ou documentação
  equivalente.

## 13. Encarregado (DPO)

- O Operador designa **Encarregado pelo Tratamento de Dados Pessoais**,
  contato:
  - E-mail: <danielnovaisnutricionista@gmail.com>
- O Cliente deve manter atualizado seu próprio Encarregado, quando
  obrigatório.

## 14. Responsabilidades

- Cada parte é responsável pelas obrigações que lhe são imputáveis
  conforme a LGPD.
- Obrigações de confidencialidade, indenização e limitação de
  responsabilidade seguem o disposto no MSA e no [EULA.md](EULA.md).
- O Operador não é responsável por descumprimento da LGPD pelo
  Controlador, incluindo ausência de base legal, falta de transparência
  com titulares ou inserção indevida de dados sensíveis.

## 15. Vigência

Este DPA vigora enquanto houver tratamento de dados pessoais pelo
Operador em nome do Controlador, sobrevivendo ao término do contrato
principal nas obrigações que, por sua natureza, devam permanecer
(confidencialidade, eliminação, notificação de incidentes em curso).

## 16. Lei aplicável e foro

- Lei brasileira (LGPD, CDC quando aplicável, demais normas setoriais).
- Foro de **Brasília/DF**, salvo acordo distinto em Order Form ou
  exigência legal aplicável a consumidor pessoa física.

## 17. Aviso legal

Este resumo executivo **não substitui** DPA assinado entre as partes.
Para tratamentos de alto risco, dados sensíveis ou volumes relevantes,
recomenda-se a celebração formal mediante revisão jurídica.
