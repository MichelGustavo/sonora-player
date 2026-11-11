# SONORA — Music Library

Player de música desktop em Python, com interface escura e minimalista.

## Recursos

- Seleção de uma pasta e leitura recursiva de arquivos de áudio.
- Biblioteca de faixas e seção exclusiva de álbuns.
- Agrupamento por **artista do álbum + álbum**, usando metadados.
- Capa incorporada ao arquivo exibida no player e nos cartões de álbum.
- Reordenação de faixas no detalhe do álbum arrastando a alça no lado direito da linha; a posição de destino é destacada durante o gesto e a numeração é salva nos metadados. Disponível quando a busca está limpa e para álbuns de um único disco.
- Reprodução, pausa, faixa anterior/próxima, busca na biblioteca, barra de progresso e volume.
- Modos de repetição: desligado, repetir fila e repetir faixa atual.
- Modo aleatório, que embaralha a fila mantendo a faixa atual (desligar restaura a ordem original).
- Tela **Tocando agora** ocupando a janela inteira: capa grande sobre fundo desfocado, título, artista, álbum, ano e gênero, progresso com tempo decorrido/restante/total, controles, volume, atalhos para ir ao álbum e editar metadados, e a lista **A seguir** com as próximas faixas (clique para tocar).
- Editor de metadados: título, artista, álbum, artista do álbum, ano/data, número da faixa e gênero.
- Opção de incorporar uma nova capa ao arquivo (formatos compatíveis).
- Atalhos: `Ctrl+O` abre pasta, `Ctrl+E` edita a faixa selecionada, `Ctrl+F` foca a busca, `Espaço` reproduz/pausa, `Ctrl+P` abre/fecha a tela Tocando agora e `F11` a abre em tela cheia.
  Na tela Tocando agora: `Esc` fecha, `←`/`→` avançam ou voltam 5 s, `Ctrl+←`/`Ctrl+→` trocam de faixa, `↑`/`↓` ajustam o volume, `M` silencia, `S` alterna o aleatório e `R` a repetição.
- Última pasta selecionada salva nas configurações do aplicativo.

## Interface

- Tema escuro com cor de destaque própria (coral), tipografia e alturas padronizadas.
- Ícones desenhados em código, alinhados e com estados de hover, ativo e desativado consistentes.
- Barra do player em três zonas: faixa atual (esquerda), controles e progresso (centro) e volume (direita).
  Clique na barra de progresso ou de volume para saltar direto à posição; o ícone de volume silencia/reativa o som.
- Cartões de álbum que se reorganizam e se ajustam à largura da janela; tamanho mínimo da janela: 900 × 620.
- A faixa em reprodução fica destacada nas tabelas; estados vazios e de "sem resultados" nas buscas.
- Falhas de leitura de arquivos, de gravação de tags/capa e de reprodução são mostradas de forma explícita
  (aviso discreto sobre o player ou diálogo com detalhes), nunca como sucesso.

## Instalação

Requer Python 3.10 ou superior.

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

### macOS / Linux

Para iniciar pelo script, ele cria o ambiente virtual local e instala as dependências
de `requirements.txt` se ainda estiverem faltando:

```bash
./run_linux.sh
```

Ou configure o ambiente manualmente:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Formatos

A biblioteca reconhece arquivos com extensão MP3, FLAC, WAV, M4A, AAC, OGG, OPUS, WMA, AIFF e MP4. A reprodução depende dos codecs disponíveis no backend multimídia do Qt e do sistema operacional; por isso, a disponibilidade efetiva pode variar entre Windows, macOS e Linux.

A leitura/gravação de tags usa Mutagen. A edição da capa incorporada é implementada para MP3/WAV/AIFF, FLAC, M4A/MP4 e OGG/Opus. Alguns arquivos com proteção, tags incomuns ou permissões restritas podem impedir a gravação.

## Observações

- O SONORA altera os metadados diretamente nos arquivos de áudio. É recomendável manter um backup da coleção antes de editar tags em lote.
- O aplicativo não envia músicas para a internet; a biblioteca é lida localmente.
- A pasta é percorrida incluindo suas subpastas.
