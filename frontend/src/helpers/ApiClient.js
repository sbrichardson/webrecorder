import superagent from 'superagent';
import config from '../config';

const methods = ['get', 'post', 'put', 'patch', 'del'];

function formatUrl(path) {
  const adjustedPath = path[0] !== '/' ? `/${path}` : path;
  if (__SERVER__) {
    // on the server use internal network
    return `http://${config.internalApiHost}:${config.internalApiPort}${adjustedPath}`;
  }
  // client side use current host
  return `${adjustedPath}`;
}

export default class ApiClient {
  constructor(req) {
    // eslint-disable-next-line no-return-assign
    methods.forEach(method =>
      ApiClient.prototype[method] = (path, { params, data } = {}, dataType = false) => new Promise((resolve, reject) => {
        const request = superagent[method](formatUrl(path));

        console.log('requesting', formatUrl(path));

        if (params) {
          request.query(params);
        }

        if (__SERVER__) {
          if (req.get('cookie')) {
            request.set('cookie', req.get('cookie'));
          }

          /*
            in order to pass `redir_host` check in wr app we need to set
            the Host header explicitly, otherwise internal network host
            is set and request is redirected
          */
          if (process.env.APP_HOST) {
            request.set({ 'Host': process.env.APP_HOST });
          }
        }

        if (data) {
          console.log('sending data..', data);

          if(dataType) {
            request.type(dataType);
          }

          request.send(data);
        }
        // eslint-disable-next-line no-confusing-arrow
        request.end((err, { body } = {}) => err ? reject(body || err) : resolve(body));
      }));
  }
}
