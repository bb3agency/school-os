import axe from "axe-core";

/**
 * Landmarks that share a role and a name (axe `landmark-unique`, best practice under WCAG
 * 1.3.1 / 2.4.1): a screen-reader landmark list that reads "Schools region, Schools region"
 * cannot tell the two apart. Returns "<tag> “name”" lines for the offenders; empty when sound.
 */
export async function duplicateLandmarks(root: Element = document.body): Promise<string[]> {
  const result = await axe.run(root, { runOnly: ["landmark-unique"] });
  return result.violations.flatMap((violation) =>
    violation.nodes.map((node) => node.html.slice(0, 120)),
  );
}
