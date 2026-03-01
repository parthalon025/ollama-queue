// Minimal preact mock for jest — only h and Fragment needed for babel-transformed JSX
function h(type, props) { return { type, props }; }
function Fragment(props) { return props.children; }
module.exports = { h, Fragment };
