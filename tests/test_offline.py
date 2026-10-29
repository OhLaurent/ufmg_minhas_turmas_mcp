from ufmg_mcp.files import extract_text, html_to_text
from ufmg_mcp.moodle import _flatten
from ufmg_mcp.portal import parse_portal, semestres

PORTAL_HTML = """
<div class="card">
  <div class="card-header"><h2 class="mb-0"><button data-target="#collapse0">Colegiados/Turmas adicionais</button></h2></div>
  <div id="collapse0" class="collapse"><ul class="card-body">
    <li class="card-text"><a href="https://virtual.ufmg.br/plataforma/course/view.php?id=25">COLEGIADO</a></li>
  </ul></div>
</div>
<div class="card">
  <div class="card-header"><h2 class="mb-0"><button data-target="#collapse3">2026/2o Semestre</button></h2></div>
  <div id="collapse3" class="collapse"><ul class="card-body">
    <li class="card-text"><a href="https://virtual.ufmg.br/20262/course/view.php?id=12368">2026_2 - EMPREENDIMENTOS - METATURMA</a></li>
    <li class="card-text"><a href="https://virtual.ufmg.br/20262/course/view.php?id=901">2026_2 - MONOGRAFIA - TC</a></li>
  </ul></div>
</div>
<div class="card">
  <div class="card-header"><h2 class="mb-0"><button data-target="#collapse13">2026/1o Semestre</button></h2></div>
  <div id="collapse13" class="collapse"><ul class="card-body">
    <li class="card-text"><a href="https://virtual.ufmg.br/20261/course/view.php?id=8608">2026_1 - ADM FINANCEIRA - TZ</a></li>
  </ul></div>
</div>
"""


def test_parse_portal():
    turmas = parse_portal(PORTAL_HTML)
    assert [t.chave for t in turmas] == ["plataforma:25", "20262:12368", "20262:901", "20261:8608"]
    t = turmas[1]
    assert t.base == "https://virtual.ufmg.br/20262"
    assert t.course_id == 12368
    assert t.grupo == "2026/2o Semestre"
    assert semestres(turmas) == ["2026/2o Semestre", "2026/1o Semestre"]


def test_flatten():
    params = {"courseids": [1, 2], "plugindata": {"onlinetext_editor": {"text": "oi", "format": 1}}, "x": True, "n": None}
    assert _flatten(params) == {
        "courseids[0]": "1",
        "courseids[1]": "2",
        "plugindata[onlinetext_editor][text]": "oi",
        "plugindata[onlinetext_editor][format]": "1",
        "x": "1",
    }


def test_extract_text():
    assert extract_text(b"ola", "a.txt") == "ola"
    assert extract_text(b"<p>Oi</p><script>x()</script>", "a.html") == "Oi"
    assert html_to_text("<p>a</p><p>b</p>") == "a\nb"
