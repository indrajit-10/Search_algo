// Run the page's ranking engine in node and print the same shape the Python CLI prints,
// so the two can be diffed. Cuts the script at the rendering section.
import fs from "fs";
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.split("<script>\n")[1].split("</script>")[0];
const engine = script.split("/* ============================ rendering")[0];
const data = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const queries = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const mod = new Function(`${engine}
  return { buildIndex, runQuery, IX, andKeywordHits };`)();
mod.buildIndex(data);
mod.IX.today = Number(process.argv[5]);
const out = [];
for (const q of queries) {
  const f = mod.runQuery(q);
  out.push({
    query: q, understood: f.understood, corrections: f.corrections,
    dated: f.dated, keyword: f.keyword, candidates: f.candidates, kept: f.kept,
    fallback: f.fallback,
    phrases: f.phrases.map(([ph, t]) => [ph, t.map(x => x[0])]),
    intent: f.why.map(p => [data.codes[p.code], p.score]),
    top: f.results.slice(0, 6).map(r => [
      "C" + String(data.docs[r.card][0]).padStart(6, "0"),
      data.codes[r.code], +r.score.toFixed(3)]),
  });
}
console.log(JSON.stringify(out, null, 1));
