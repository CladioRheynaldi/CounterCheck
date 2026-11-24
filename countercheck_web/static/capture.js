class Capture extends AudioWorkletProcessor {
  constructor(){super();this.buffer=new Float32Array(1600);this.offset=0;}
  process(inputs,outputs){
    const input=inputs[0];
    if(input&&input[0]){
      for(let i=0;i<input[0].length;i++){
        let value=0;for(let c=0;c<input.length;c++)value+=input[c][i]/input.length;
        this.buffer[this.offset++]=value;
        if(this.offset===1600){let frame=this.buffer;this.port.postMessage(frame.buffer,[frame.buffer]);this.buffer=new Float32Array(1600);this.offset=0;}
      }
    }
    for(const channel of outputs[0]||[])channel.fill(0);
    return true;
  }
}
registerProcessor('countercheck-capture',Capture);
