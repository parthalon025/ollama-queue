// Minimal preact mock for jest — h captures type, props, and children for structural assertions
function h(type, props, ...children) {
  const mergedProps = props ? { ...props } : {};
  if (children.length === 1) mergedProps.children = children[0];
  else if (children.length > 1) mergedProps.children = children;
  return { type, props: mergedProps };
}
function Fragment(props) { return props.children; }
module.exports = { h, Fragment };
